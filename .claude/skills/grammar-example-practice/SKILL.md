---
name: grammar-example-practice
description: Makes practice example sentences for an English grammar or syntax structure, based on the user's own course lectures (the English Syntax book scans in lectures/). It first finds the lecture page that explains the structure, then writes a new sentence that fits that page's definition, then delivers an HTML practice page where the answer stays hidden until the user reveals it. Use this whenever the user asks for an example, model or practice sentence with a particular structure: a clause type (embedded, subordinate, relative, nominal, adverb, non-finite), a phrase type or modifier (adjective phrase, N-bar, post-modifier, complement), a verb pattern (intensive, complex transitive, ditransitive), or a sentence type (complex, compound-complex). Also use it when they say "check the lecture first" or want grammar exercises with hidden answers, even if they don't mention HTML.
---

# Grammar example practice

The user is an English-major student. Their syntax course follows one textbook, scanned into PDFs in `lectures/`, and the lecturer's terminology and analysis differ from other grammars. For example, the book separates an adjective's *complement* from its *post-modifier*, and *embedded* from *subordinate* clauses. An example sentence is only useful to them if it fits **their** lecture's definition, and they want to try the structure themselves before seeing an answer.

So every request goes through three steps:
1. Find the lecture page.
2. Write a sentence from that page.
3. Put the answer in a page where it stays hidden.

Keep the answer out of the chat. If your reply shows the sentence, the user can't try it first, so the reply only says which lecture page you used and where the practice page is. Give the answer in chat only if they ask for it directly.

All commands below run from the project root. `scripts/` and `references/` are inside this skill's folder.

## Step 1: Find the lecture page that explains the structure

**a. Locate candidate pages.** For the course book, start with `references/english_syntax_book.md`. It maps every section to its book page and PDF page, and explains the book's notation. Read it on the first request in a conversation. Then confirm and widen the search with the full-text index of the lectures:

```bash
python3 <skill-dir>/scripts/lecture_search.py search "embedded" "adverb clause" "adjective complement" "post-modifier" "headA"
```

Search with the user's words plus the book's own terms and abbreviations, because the book often names a structure differently from the request. Some pairs:

| The user says | Also search |
|---|---|
| embedded clause | subordinate clause, dependent clause, finite clause, EmNCl / EmACl / EmAdvCl |
| … modifying an adjective (phrase) | adjective complement, post-adjectival modifier, headA, AP |
| relative clause | adjective clause, SubACl, EmACl |
| that-clause / wh-clause | nominal clause, EmNCl, Comp |
| to-infinitive / -ing clause | non-finite clause, infinitive clause, gerund clause, participial |
| object complement, subject complement | oC, sC, complex transitive, intensive |

The first run of `search` builds an OCR index of the scanned PDFs, which takes about two minutes. It needs `tesseract` and `pdftoppm` (Linux: `apt-get install tesseract-ocr poppler-utils`; macOS: `brew install tesseract poppler`). If they can't be installed, skip the index and go straight to the pages the book map points to. `show FILE PAGE --context 1` prints a page's OCR text, and `outline FILE` prints the first lines of every page.

**b. Look at the page itself.** OCR text is good for finding a page, but it scrambles tree diagrams and loses the book's brackets and small capitals. Before writing anything, open the chosen PDF page(s) with the Read tool, e.g. `pages: "2-3"`. Read the neighbouring page too when a section starts or ends mid-page. Pick the page that *explains* the structure (definition, rule, tree, worked examples), not one that only mentions it. Check the answer key (Answers for Exercises, book pp. 166-212) when the structure has exercises there.

**c. Note what you'll need:**
- file, PDF page and printed book page(s), and the section number and title
- the rule, quoted word for word (one or two sentences)
- two or three of the lecture's own example sentences
- the labels the book uses to analyse it, from its trees

**d. If the request is ambiguous for this book, resolve it.** For example, "a clause modifying an adjective" could be the adjective's *complement* (obligatory: *afraid that…*, *aware that…*) or its optional *post-modifier* (*anxious that…*, *glad that…*), and the book treats these differently (§28-31). Choose the reading the request's wording supports, or the one the matching section teaches. Say which one in the explanation, and offer the other reading as a second exercise when both fit.

**If nothing in the lectures covers it:** say where you searched and with which terms, and ask the user to name the page or add the lecture file to `lectures/`. Don't silently fall back to general grammar knowledge, because the user asked for their lecture's version. If they then tell you to go ahead anyway, mark the exercise's source as not found (see Step 3) so the page shows that it isn't from their lecture. If Google Drive tools are available and the user keeps lectures there, you can search Drive too (`fullText contains '<term>'`) and read the matching file.

## Step 2: Write the sentence

- **Follow the lecture's definition,** checked against its examples. The lecture is the authority even where other grammars disagree.
- **Write a new sentence.** Don't reuse or lightly edit the book's examples; the user has those already. Choose a fresh adjective, verb or topic.
- **Make the target structure the only hard part.** Use a natural, everyday sentence of moderate length, without extra embedded clauses the request didn't ask for.
- **Write one sentence per structure** unless the user asks for more. A request naming several structures gets one exercise each.
- **Check it before building the page:**
  - Find the exact words that form the structure.
  - Confirm that span does the job the user asked for: it attaches to the head they named (the adjective, not a noun or the verb) and has the right form (finite or non-finite, *that*-clause or *wh*-clause).
  - Confirm it matches the lecture's rule and is grammatical.
  - Write the analysis using the book's labels.

## Step 3: Build the practice page

Write a JSON file next to where the page will go, e.g. `grammar-practice/<topic>.json`, then build the page:

```bash
python3 <skill-dir>/scripts/build_practice_page.py grammar-practice/<topic>.json grammar-practice/<topic>.html
```

The JSON shape (the builder's docstring has the full field list):

```json
{
  "title": "Practice: <topic in the user's words>",
  "exercises": [
    {
      "structure": "<the structure, in the user's words>",
      "source": {
        "found": true,
        "file": "EnglishSyntax2.pdf",
        "pdf_page": 0,
        "book_pages": "<printed page(s)>",
        "section": "<section number and title>",
        "rule": "<the rule, quoted word for word>",
        "lecture_examples": ["<one of the book's examples>", "<another>"]
      },
      "task": "Write a sentence in which <what the user must produce, in the lecture's terms>.",
      "hint": "<a nudge that doesn't give the sentence away, e.g. which group of adjectives the lecture lists>",
      "answers": [
        {
          "sentence": "<your new sentence>",
          "highlight": ["<the exact words that form the structure>"],
          "analysis": "<labelled bracketing with the book's labels>",
          "explanation": "<2-4 sentences: which part of the rule the sentence meets, why the highlighted span counts, and any distinction from the lecture, e.g. complement vs. modifier>"
        }
      ]
    }
  ]
}
```

- `source` can be a list when the explanation spans two sections.
- For a structure not found in the lectures, use `"source": {"found": false, "searched": "<files and terms>"}`.
- `highlight` must be copied exactly from `sentence`; the builder stops with an error if it isn't.

The page shows each exercise's lecture reference and task, a box for the user's own attempt, a collapsed hint and a collapsed answer, plus "Show all / Hide all answers" buttons. The answers use `<details>`, so they stay hidden even where JavaScript is blocked.

If you have a tool for sending files to the user, send the HTML so it opens for them. Otherwise give its path.

## The chat reply

Keep it short and leave the sentence out, e.g.:

> I found it in **EnglishSyntax2.pdf, PDF page 2-3 (book pp. 107-108), §19.3.3.1**: a finite *that/wh*-clause or a non-finite *to*-clause can complement or modify a head adjective in a predicative AP. Your practice page is at `grammar-practice/adjective-clause.html`. Write your own sentence in the box first, then open **Show answer**.

If you had to choose between two readings (Step 1d), say in one line which one you used.
