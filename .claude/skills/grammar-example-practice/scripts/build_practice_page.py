#!/usr/bin/env python3
"""Turn an exercises JSON file into an HTML practice page with hidden answers.

  python3 build_practice_page.py exercises.json [output.html]

Without an output path the page goes to grammar-practice/<title-slug>-<date>.html.
The answers are collapsed <details> blocks, so they stay hidden until the
reader opens them, even if the browser blocks JavaScript.

JSON shape (fields marked ? are optional):

{
  "title"?: "Practice: embedded clauses in adjective phrases",
  "exercises": [
    {
      "structure": "Embedded clause modifying an adjective phrase",
      "source": {                      # or a list of these, or {"found": false, "searched"?: "..."}
        "found": true,
        "file": "EnglishSyntax2.pdf",
        "pdf_page"?: 3,
        "book_pages"?: "107-108",
        "section"?: "19.3.3.1 Subordinate/Embedded adverb clauses",
        "rule": "Short verbatim quote of the lecture's definition",
        "lecture_examples"?: ["They were hopeful (that the weather would change)."]
      },
      "task": "Write a sentence in which ...",
      "hint"?: "...",
      "answers": [
        {
          "sentence": "Linh was nervous that the bus would leave without her.",
          "highlight"?: ["that the bus would leave without her"],
          "analysis"?: "[S [NP Linh] [VP ...]]",
          "explanation": "..."
        }
      ]
    }
  ]
}
"""

import datetime
import html
import json
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "template.html"


def esc(text):
    return html.escape(str(text), quote=True)


def para(text):
    return "<br>".join(esc(line) for line in str(text).strip().splitlines())


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "practice"


def highlighted(sentence, spans, where, problems):
    """Escape the sentence and wrap each highlight span in <mark>."""
    if isinstance(spans, str):
        spans = [spans]
    ranges = []
    for span in spans or []:
        start = sentence.find(span)
        if start < 0:
            problems.append(f"{where}: highlight {span!r} is not part of the sentence")
            continue
        ranges.append((start, start + len(span)))
    ranges.sort()
    for (a1, b1), (a2, _) in zip(ranges, ranges[1:]):
        if a2 < b1:
            problems.append(f"{where}: highlights overlap ({sentence[a1:b1]!r})")
            return esc(sentence)
    out, pos = [], 0
    for a, b in ranges:
        out.append(esc(sentence[pos:a]))
        out.append(f"<mark>{esc(sentence[a:b])}</mark>")
        pos = b
    out.append(esc(sentence[pos:]))
    return "".join(out)


def render_source(source, where, problems):
    sources = source if isinstance(source, list) else [source]
    if not sources or not all(isinstance(s, dict) for s in sources):
        problems.append(f"{where}: 'source' must be an object or a list of objects")
        return ""
    if any(s.get("found") is False for s in sources):
        searched = next((s.get("searched") for s in sources if s.get("searched")), "")
        extra = f"<p class=\"lecture-examples\">Searched: {esc(searched)}</p>" if searched else ""
        return ("    <section class=\"source warn\">\n"
                "      <p class=\"label\">Source</p>\n"
                "      <p class=\"cite\">Not found in your lectures: based on general grammar knowledge.</p>\n"
                f"      {extra}\n    </section>\n")
    blocks = []
    for i, s in enumerate(sources, 1):
        for key in ("file", "rule"):
            if not s.get(key):
                problems.append(f"{where}: source {i} is missing '{key}'")
        cite = [s.get("file", "")]
        if s.get("pdf_page"):
            cite.append(f"PDF page {s['pdf_page']}")
        if s.get("book_pages"):
            cite.append(f"book p. {s['book_pages']}")
        if s.get("section"):
            cite.append(s["section"])
        examples = s.get("lecture_examples") or []
        ex_html = ""
        if examples:
            ex_html = ("\n      <p class=\"lecture-examples\">Lecture examples: "
                       + " &middot; ".join(f"<em>{esc(e)}</em>" for e in examples) + "</p>")
        blocks.append(
            "    <section class=\"source\">\n"
            "      <p class=\"label\">From your lecture</p>\n"
            f"      <p class=\"cite\">{' &middot; '.join(esc(c) for c in cite if c)}</p>\n"
            f"      <blockquote>{para(s.get('rule', ''))}</blockquote>{ex_html}\n"
            "    </section>\n"
        )
    return "".join(blocks)


def render_exercise(n, ex, problems):
    where = f"exercise {n}"
    for key in ("structure", "source", "task", "answers"):
        if key not in ex:
            problems.append(f"{where}: missing '{key}'")
    answers = ex.get("answers") or []
    if not isinstance(answers, list) or not answers:
        problems.append(f"{where}: 'answers' must be a non-empty list")
        answers = []

    parts = [
        "  <article class=\"card\">\n",
        f"    <p class=\"eyebrow\">Exercise {n}</p>\n",
        f"    <h2>{esc(ex.get('structure', ''))}</h2>\n",
        render_source(ex.get("source", {}), where, problems),
        f"    <p class=\"task\"><strong>Your turn:</strong> {para(ex.get('task', ''))}</p>\n",
        f"    <textarea aria-label=\"Your sentence for exercise {n}\" "
        "placeholder=\"Write your own sentence here first...\"></textarea>\n",
    ]
    if ex.get("hint"):
        parts.append(
            "    <details class=\"hint\"><summary><span class=\"when-closed\">Show hint</span>"
            "<span class=\"when-open\">Hide hint</span></summary>\n"
            f"      <p>{para(ex['hint'])}</p>\n    </details>\n"
        )
    label = "answer" if len(answers) == 1 else f"{len(answers)} answers"
    parts.append(
        "    <details class=\"answer\"><summary><span class=\"when-closed\">Show " + label + "</span>"
        "<span class=\"when-open\">Hide " + label + "</span></summary>\n"
    )
    for i, ans in enumerate(answers, 1):
        aw = f"{where}, answer {i}"
        if not ans.get("sentence"):
            problems.append(f"{aw}: missing 'sentence'")
        if not ans.get("explanation"):
            problems.append(f"{aw}: missing 'explanation'")
        sentence = highlighted(ans.get("sentence", ""), ans.get("highlight"), aw, problems)
        parts.append("      <div class=\"answer-body\">\n")
        parts.append(f"        <p class=\"sentence\">{sentence}</p>\n")
        if ans.get("analysis"):
            parts.append(f"        <pre class=\"analysis\">{esc(ans['analysis'])}</pre>\n")
        parts.append(f"        <p class=\"explanation\">{para(ans.get('explanation', ''))}</p>\n")
        parts.append("      </div>\n")
    parts.append("    </details>\n  </article>\n")
    return "".join(parts)


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    exercises = data.get("exercises") or []
    if not exercises:
        raise SystemExit("The JSON has no 'exercises'.")

    title = data.get("title") or ("Practice: " + exercises[0].get("structure", "grammar"))
    count = f"{len(exercises)} exercise" + ("s" if len(exercises) != 1 else "")
    problems = []
    body = "".join(render_exercise(n, ex, problems) for n, ex in enumerate(exercises, 1))
    if problems:
        raise SystemExit("Fix these before building the page:\n- " + "\n- ".join(problems))

    today = datetime.date.today().isoformat()
    page = (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{TITLE}}", esc(title))
            .replace("{{SUBTITLE}}", esc(count + ". Answers are hidden until you open them."))
            .replace("{{DATE}}", today)
            .replace("{{EXERCISES}}", body))

    out = Path(sys.argv[2]) if len(sys.argv) == 3 else Path("grammar-practice") / f"{slugify(title)}-{today}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
