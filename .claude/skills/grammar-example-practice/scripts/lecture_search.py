#!/usr/bin/env python3
"""Find the lecture page that explains a grammar structure.

Lecture files live in one folder (default: lectures/ at the project root).
Supported: .pdf (text or scanned), .pptx, .docx, .txt, .md.

Scanned PDFs have no text layer, so the first run reads them with Tesseract OCR
and caches the text page by page in <dir>/.index/. Later runs reuse the cache
until a file changes, so only the first run is slow.

  python3 lecture_search.py index   [--dir lectures]
  python3 lecture_search.py search  [--dir lectures] [--top 8] TERM [TERM ...]
  python3 lecture_search.py show    [--dir lectures] FILE PAGE [--context 1]
  python3 lecture_search.py outline [--dir lectures] [FILE]

OCR text is good enough to locate a page but loses tree diagrams and bracket
notation, so look at the page itself before relying on its analysis.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED = {".pdf", ".pptx", ".docx", ".txt", ".md"}
INDEX_DIR = ".index"
INDEX_VERSION = 1
MIN_TEXT_CHARS = 40  # a PDF page with less text than this is treated as scanned

NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- extraction

def pdf_text_pages(path):
    """Text layer of each page; empty strings when the PDF is a scan."""
    try:
        from pypdf import PdfReader
        return [p.extract_text() or "" for p in PdfReader(str(path)).pages]
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, check=True,
        ).stdout
        pages = out.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        return pages
    raise RuntimeError("cannot read PDFs: install pypdf (pip install pypdf) or poppler (pdftotext)")


def ocr_page(path, page_no):
    # Pages are OCR'd in parallel, so each Tesseract gets one thread; letting
    # every process start its own OpenMP pool stalls the machine.
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "p")
        subprocess.run(
            ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page_no), "-l", str(page_no),
             str(path), prefix],
            capture_output=True, check=True,
        )
        images = sorted(Path(tmp).glob("p*.png"))
        if not images:
            return ""
        return subprocess.run(
            ["tesseract", str(images[0]), "stdout", "-l", "eng", "--psm", "3"],
            capture_output=True, text=True, check=True, env=env,
        ).stdout


def pdf_pages(path):
    texts = pdf_text_pages(path)
    scanned = [i for i, t in enumerate(texts) if len(t.strip()) < MIN_TEXT_CHARS]
    ocr_flags = [False] * len(texts)
    if scanned:
        missing = [tool for tool in ("pdftoppm", "tesseract") if not shutil.which(tool)]
        if missing:
            log(f"  {path.name}: {len(scanned)} scanned page(s) need OCR but {', '.join(missing)} "
                "is not installed; those pages stay empty in the index. Install Tesseract and "
                "poppler, or view the pages directly.")
        else:
            log(f"  {path.name}: OCR on {len(scanned)} scanned page(s), this runs once...")
            done = 0

            def work(i):
                return i, ocr_page(path, i + 1)

            with ThreadPoolExecutor(max_workers=max(1, os.cpu_count() or 1)) as pool:
                for i, text in pool.map(work, scanned):
                    texts[i] = text
                    ocr_flags[i] = True
                    done += 1
                    if done % 10 == 0 or done == len(scanned):
                        log(f"    {done}/{len(scanned)} pages")
    return [{"n": i + 1, "label": f"page {i + 1}", "text": t, "ocr": ocr_flags[i]}
            for i, t in enumerate(texts)]


def pptx_pages(path):
    with zipfile.ZipFile(path) as z:
        rels = ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.iter(NS_REL + "Relationship")}
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        slide_ids = [el.get(NS_R + "id") for el in pres.iter(NS_P + "sldId")]

        def part(base_dir, target):
            if target.startswith("/"):
                return target.lstrip("/")
            return os.path.normpath(os.path.join(base_dir, target)).replace(os.sep, "/")

        def paragraphs(xml_bytes):
            root = ET.fromstring(xml_bytes)
            out = []
            for p in root.iter(NS_A + "p"):
                text = "".join(t.text or "" for t in p.iter(NS_A + "t"))
                if text.strip():
                    out.append(text)
            return out

        pages = []
        for n, rid in enumerate(slide_ids, 1):
            slide_part = part("ppt", targets[rid])
            lines = paragraphs(z.read(slide_part))
            rel_part = part(os.path.dirname(slide_part), "_rels/" + os.path.basename(slide_part) + ".rels")
            if rel_part in z.namelist():
                for r in ET.fromstring(z.read(rel_part)).iter(NS_REL + "Relationship"):
                    if r.get("Type", "").endswith("/notesSlide"):
                        notes = paragraphs(z.read(part(os.path.dirname(slide_part), r.get("Target"))))
                        notes = [t for t in notes if not t.strip().isdigit()]
                        if notes:
                            lines.append("[Speaker notes] " + " ".join(notes))
            pages.append({"n": n, "label": f"slide {n}", "text": "\n".join(lines), "ocr": False})
    return pages


def docx_pages(path):
    """Pages from Word's page-break markers; 15-paragraph chunks if there are none."""
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(NS_W + "body")
    paras, breaks_before = [], []
    for p in body.iter(NS_W + "p"):
        brk = any(
            el.tag == NS_W + "lastRenderedPageBreak"
            or (el.tag == NS_W + "br" and el.get(NS_W + "type") == "page")
            for el in p.iter()
        )
        breaks_before.append(brk)
        paras.append("".join(t.text or "" for t in p.iter(NS_W + "t")))
    if any(breaks_before[1:]):
        pages, current = [], []
        for text, brk in zip(paras, breaks_before):
            if brk and current:
                pages.append(current)
                current = []
            if text.strip():
                current.append(text)
        pages.append(current)
        return [{"n": i, "label": f"page ~{i}", "text": "\n".join(c), "ocr": False}
                for i, c in enumerate(pages, 1)]
    kept = [t for t in paras if t.strip()]
    return [{"n": i // 15 + 1, "label": f"paragraphs {i + 1}-{min(i + 15, len(kept))}",
             "text": "\n".join(kept[i:i + 15]), "ocr": False}
            for i in range(0, len(kept), 15)]


def text_pages(path):
    raw = path.read_text(encoding="utf-8", errors="replace")
    if "\f" in raw:
        return [{"n": i, "label": f"page {i}", "text": t, "ocr": False}
                for i, t in enumerate(raw.split("\f"), 1)]
    lines = raw.splitlines()
    return [{"n": i // 40 + 1, "label": f"lines {i + 1}-{min(i + 40, len(lines))}",
             "text": "\n".join(lines[i:i + 40]), "ocr": False}
            for i in range(0, len(lines), 40)]


EXTRACTORS = {".pdf": pdf_pages, ".pptx": pptx_pages, ".docx": docx_pages,
              ".txt": text_pages, ".md": text_pages}


# --------------------------------------------------------------------- index

def lecture_files(folder):
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED
        and not p.name.startswith(("~$", "."))
        and INDEX_DIR not in p.relative_to(folder).parts
    )


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_index(folder):
    """Return [(relative name, pages)], (re)building stale entries."""
    files = lecture_files(folder)
    if not files:
        raise SystemExit(f"No lecture files ({', '.join(sorted(SUPPORTED))}) found in {folder}/")
    cache_dir = folder / INDEX_DIR
    cache_dir.mkdir(exist_ok=True)
    result = []
    for path in files:
        rel = path.relative_to(folder).as_posix()
        cache = cache_dir / (rel.replace("/", "__") + ".json")
        digest = sha256(path)
        if cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("sha256") == digest and data.get("version") == INDEX_VERSION:
                    result.append((rel, data["pages"]))
                    continue
            except (ValueError, KeyError):
                pass
        log(f"Indexing {rel} ...")
        try:
            pages = EXTRACTORS[path.suffix.lower()](path)
        except Exception as exc:  # one unreadable file should not stop the search
            log(f"  skipped {rel}: {exc}")
            continue
        cache.write_text(json.dumps({"version": INDEX_VERSION, "source": rel, "sha256": digest,
                                     "pages": pages}, ensure_ascii=False), encoding="utf-8")
        result.append((rel, pages))
    return result


# -------------------------------------------------------------------- search

def normalise(text):
    text = re.sub(r"-\s*\n\s*", "", text)          # re-join words hyphenated across lines
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text)


def term_pattern(term):
    words = [re.escape(w) for w in normalise(term).split()]
    body = r"[\s\-]+".join(words)
    plural = r"(?:e?s)?" if re.search(r"[a-z]$", term.lower()) else ""
    return re.compile(r"(?<!\w)" + body + plural + r"(?!\w)", re.IGNORECASE)


def snippet(text, start, end, width=160):
    a, b = max(0, start - width), min(len(text), end + width)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def cmd_search(args):
    index = load_index(args.dir)
    patterns = [(t, term_pattern(t)) for t in args.terms]
    hits = []
    for rel, pages in index:
        for page in pages:
            text = normalise(page["text"])
            counts, first = {}, None
            for term, pat in patterns:
                found = list(pat.finditer(text))
                if found:
                    counts[term] = len(found)
                    if first is None or found[0].start() < first[0]:
                        first = (found[0].start(), found[0].end())
            if counts:
                score = sum(min(c, 5) for c in counts.values()) + 3 * (len(counts) - 1)
                hits.append((len(counts), score, rel, page, counts, snippet(text, *first)))
    total_pages = sum(len(p) for _, p in index)
    print(f"Searched {len(index)} file(s), {total_pages} page(s) for: " + " | ".join(args.terms))
    if not hits:
        print("No matches. Try other wording (synonyms, abbreviations such as AdjP/AP, CP/S').")
        return
    hits.sort(key=lambda h: (-h[0], -h[1], h[2], h[3]["n"]))
    for rank, (n_terms, score, rel, page, counts, snip) in enumerate(hits[:args.top], 1):
        found = ", ".join(f"{t} x{c}" for t, c in counts.items())
        tag = " (OCR)" if page.get("ocr") else ""
        print(f"\n{rank}. {rel}  {page['label']}{tag}  terms {n_terms}/{len(patterns)}  [{found}]")
        print(f"   {snip}")
    if len(hits) > args.top:
        print(f"\n({len(hits) - args.top} more page(s) matched; raise --top to see them)")


def find_file(index, name):
    exact = [(rel, pages) for rel, pages in index if rel == name]
    stem = [(rel, pages) for rel, pages in index
            if Path(rel).stem.lower() == Path(name).stem.lower()]
    loose = [(rel, pages) for rel, pages in index if name.lower() in rel.lower()]
    match = exact or stem or loose
    if len(match) != 1:
        options = ", ".join(rel for rel, _ in (match or index))
        raise SystemExit(f"'{name}' matches {len(match)} files. Choose one of: {options}")
    return match[0]


def cmd_show(args):
    rel, pages = find_file(load_index(args.dir), args.file)
    by_n = {p["n"]: p for p in pages}
    if args.page not in by_n:
        raise SystemExit(f"{rel} has pages 1-{max(by_n)}; there is no page {args.page}")
    for n in range(args.page - args.context, args.page + args.context + 1):
        if n in by_n:
            page = by_n[n]
            tag = " (OCR text: check diagrams and brackets on the page itself)" if page.get("ocr") else ""
            print(f"=== {rel} - {page['label']}{tag} ===")
            print(page["text"].strip() or "(no text)")
            print()


def cmd_outline(args):
    index = load_index(args.dir)
    if args.file:
        index = [find_file(index, args.file)]
    for rel, pages in index:
        print(f"=== {rel} ({len(pages)} pages) ===")
        for page in pages:
            lines = [ln.strip() for ln in page["text"].splitlines() if len(ln.strip()) > 3]
            print(f"{page['label']:>12}: " + " | ".join(lines[:2])[:110])


def cmd_index(args):
    index = load_index(args.dir)
    for rel, pages in index:
        ocr = sum(1 for p in pages if p.get("ocr"))
        empty = sum(1 for p in pages if not p["text"].strip())
        print(f"{rel}: {len(pages)} pages ({ocr} via OCR, {empty} without text)")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dir", type=Path, default=Path("lectures"), help="lecture folder (default: lectures)")
        p.set_defaults(func=func)
        return p

    add("index", cmd_index, "build or refresh the page index")
    p = add("search", cmd_search, "rank pages by how many search terms they contain")
    p.add_argument("terms", nargs="+")
    p.add_argument("--top", type=int, default=8)
    p = add("show", cmd_show, "print the text of one page")
    p.add_argument("file")
    p.add_argument("page", type=int)
    p.add_argument("--context", type=int, default=0, help="also print N pages before and after")
    p = add("outline", cmd_outline, "first lines of every page, to see how a lecture is organised")
    p.add_argument("file", nargs="?")

    args = parser.parse_args()
    if not args.dir.is_dir():
        raise SystemExit(f"Lecture folder not found: {args.dir}/ (use --dir to point at it)")
    args.func(args)


if __name__ == "__main__":
    main()
