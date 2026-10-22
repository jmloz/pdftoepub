"""The AI agents of the converter.

1. Analyst  -> understands the book: metadata, subject, languages, chapter structure.
2. Layout   -> converts each chunk of pages into semantic XHTML (code, tables, notes…).
3. Reviewer -> fixes a chunk when the automatic checks detect missing content.

Prompts are written in English, but the agents always keep the book's own language:
they never translate the content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .extractor import BOX_CLOSE, BOX_OPEN, CODE_MARK, MATH_CLOSE, MATH_OPEN, Asset, Document, Page
from .llm import Engine, pdf_block

CONTINUES_MARK = "<!--CONTINUES-->"

# ---------------------------------------------------------------------------
# 1. Analyst
# ---------------------------------------------------------------------------

ANALYST_SYSTEM = """\
You are the Analyst agent of a PDF-to-EPUB book converter. You receive structural \
information about a book and decide how to split it into chapters for the EPUB.

Rules:
- Each EPUB chapter becomes a separate file. Use the book's real top-level divisions as \
chapters (preface, chapters, appendices). Inner sections will become headings inside the \
chapter, so do not list them as chapters.
- Chapters must cover the whole page range contiguously without overlapping: the next \
start_page = previous end_page + 1. Leave no gaps.
- If the PDF has an outline (bookmarks), trust it but fix broken titles or wrong levels.
- If it does not, infer chapters from the heading candidates (large text) and the sample pages.
- Set skip=true ONLY for pages that add nothing to an EPUB: the printed table of contents \
(the EPUB generates its own) and blank pages. Never skip real content; the alphabetical \
index at the end may be skipped because its page numbers are meaningless in an EPUB.
- language: BCP 47 code of the book's text language (e.g. "en", "es").
- code_languages: programming languages, shells or formats that appear in the book \
(e.g. "bash", "python", "c", "yaml", "sql", "console" for terminal sessions with a prompt).
- style_notes: conventions of the book that the layout agent must respect (how shell \
prompts are shown, "Note"/"Warning" boxes, typographic conventions, etc.).
- Write title, description and subject in the book's own language.
"""

ANALYST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "authors", "language", "publisher", "description", "subject",
                 "code_languages", "style_notes", "chapters"],
    "properties": {
        "title": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string"},
        "publisher": {"type": "string"},
        "description": {"type": "string"},
        "subject": {"type": "string"},
        "code_languages": {"type": "array", "items": {"type": "string"}},
        "style_notes": {"type": "string"},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "start_page", "end_page", "skip"],
                "properties": {
                    "title": {"type": "string"},
                    "start_page": {"type": "integer"},
                    "end_page": {"type": "integer"},
                    "skip": {"type": "boolean"},
                },
            },
        },
    },
}


@dataclass
class Chapter:
    title: str
    start: int
    end: int
    skip: bool = False


@dataclass
class Profile:
    title: str
    authors: list[str]
    language: str
    publisher: str
    description: str
    subject: str
    code_languages: list[str]
    style_notes: str
    chapters: list[Chapter]

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        chapters = [Chapter(c["title"], c["start_page"], c["end_page"], c["skip"]) for c in d["chapters"]]
        return cls(**{**d, "chapters": chapters})

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["chapters"] = [
            {"title": c.title, "start_page": c.start, "end_page": c.end, "skip": c.skip}
            for c in self.chapters
        ]
        return d

    def summary(self) -> str:
        return (
            f"Title: {self.title}\nAuthors: {', '.join(self.authors)}\nLanguage: {self.language}\n"
            f"Subject: {self.subject}\nCode languages: {', '.join(self.code_languages) or 'none'}\n"
            f"Book conventions: {self.style_notes}"
        )


def normalize_chapters(chapters: list[Chapter], first: int, last: int) -> list[Chapter]:
    """Guarantee contiguous, non-overlapping coverage, whatever the model returned."""
    chs = sorted((c for c in chapters if c.end >= c.start), key=lambda c: c.start)
    if not chs:
        return [Chapter("Content", first, last)]
    chs[0].start = first
    for previous, following in zip(chs, chs[1:]):
        previous.end = following.start - 1
    chs[-1].end = last
    return [c for c in chs if c.end >= c.start]


def analyze(engine: Engine, doc: Document, sample_pages: int = 12) -> Profile:
    first, last = doc.pages[0].number, doc.pages[-1].number
    sample = "\n\n".join(f"=== Page {p.number} ===\n{p.text[:2500]}" for p in doc.pages[:sample_pages])
    candidates = "\n".join(f"p.{page} [{size:g}pt] {text}" for page, size, text in doc.heading_candidates[:600])
    outline = "\n".join(f"{'  ' * (n - 1)}- {title} (p.{p})" for n, title, p in doc.outline) or "(the PDF has no bookmarks)"
    content = f"""\
Page range to convert: {first}–{last} (the PDF has {doc.page_count}).
Body text size: {doc.body_size:g}pt.
PDF metadata: {json.dumps(doc.metadata, ensure_ascii=False)}

## PDF bookmarks
{outline}

## Heading candidates (large text) by page
{candidates or '(none)'}

## First pages (annotated text: '{CODE_MARK}' = monospaced line, [Hn] = heading of n pt)
{sample}
"""
    profile = Profile.from_dict(engine.ask_json(ANALYST_SYSTEM, content, ANALYST_SCHEMA))
    profile.chapters = normalize_chapters(profile.chapters, first, last)
    return profile


# ---------------------------------------------------------------------------
# 2. Layout
# ---------------------------------------------------------------------------

LAYOUT_SYSTEM = """\
You are the Layout agent of a PDF-to-EPUB book converter, specialized in technical books \
(programming, Linux, networking, command-line tools). You convert a chunk of pages into \
clean, semantic XHTML for an e-book reader.

You receive: the PDF of those pages (to see the real layout) and the extracted, annotated \
text of the same pages. Use the extracted text as the literal source and the PDF to \
understand the structure (what is a heading, table, note, code, figure caption…).

CORE PRINCIPLE: total fidelity. Do not summarize, paraphrase, translate, add explanations \
or omit any content. Keep the book's original language. Your job is to restructure, not rewrite.

What you must do:
- Join broken lines into full paragraphs and rejoin words hyphenated at the end of a line \
("program-\\nming" → "programming"), except hyphens that belong to the word or to code.
- Remove PDF artifacts: repeated running headers and footers, page numbers, crop marks.
- Headings: <h1> for the chapter title (only if it appears in these pages), <h2>, <h3>, \
<h4> for sections according to their hierarchy.
- Code, commands and terminal output: ALWAYS in <pre><code class="language-XXX">…</code></pre>, \
copied character by character, keeping indentation, spaces, case, straight quotes \
(never curly), double hyphens (--option, never –), backslashes and line breaks. XXX is the \
language (bash, console, python, c, yaml, json, sql, text…). Use "console" for terminal \
sessions with a prompt ($, #, >>>) followed by their output. Lines marked with '¦ ' in the \
extracted text are in a monospaced font: drop the '¦ ' mark and keep the indentation after \
it. If a code line was wrapped by the page width (you will see it in the PDF), rejoin it; \
if the book uses '\\' or '↩' as a continuation marker, keep it.
- Inline code (command, file, option and variable names inside a paragraph): <code>.
- Tables: <table> with <thead>/<tbody>, <th> and <td>; keep every cell.
- Lists: <ul>/<ol>/<li>; definition lists (option → description) as <dl><dt><dd>.
- Notes, tips, warnings and sidebars: <aside class="note|tip|warning|sidebar"> with their \
title in <p class="aside-title">.
- Emphasis: <em> and <strong> where the original uses italics or bold (**text** in the extracted text).
- Keys: <kbd>Ctrl</kbd>+<kbd>C</kbd>.
- Images: you get a list of assets with an ID. To insert one use exactly \
<figure><img src="ID" alt="short, useful description"/><figcaption>original caption</figcaption></figure>. \
"diagram" assets are areas with vector drawings: use them only if they really are a diagram, \
chart or figure in the PDF; ignore them if they are table borders, code boxes or ornaments \
(in that case rebuild the table or code as text). Write alt text in the book's language.
- Text between {box_open} and {box_close} sits inside a drawn frame in the PDF: it is usually \
a sidebar, note, tip or warning (use <aside>), but it can also be a framed code listing \
(use <pre><code>) or a table (use <table>). Decide from the PDF.
- Pages marked as multi-column: the extracted text already follows the reading order \
(left column first). Check it against the PDF and never interleave lines from different columns.
- If a page has no extracted text (scanned), transcribe it from the PDF.
- Footnotes: place them at the end of the chunk in <aside class="footnote" id="fn-N"> and \
link them with <a href="#fn-N" class="noteref">N</a>.
- Cross-references like "see page 42": keep the text as is.

Mathematics (text between {math_open} and {math_close} was set in a math font; use the PDF to \
read the formula exactly, since extracted math symbols are often garbled):
{math_rules}

Continuity between chunks:
- You are given the end of the previous chunk. If these pages start in the middle of a \
paragraph, list or code block that came from the previous page, start your answer EXACTLY \
with {continues} followed by the continuing element (<p>…</p>, <pre><code …>…</code></pre>, \
<li>…</li>) containing only the missing part; the assembler will merge it with the previous one.
- Do not repeat content that was already in the previous chunk.

Answer format: only the XHTML fragment (no <html>, <head> or <body>, no Markdown, no ```), \
well-formed: closed tags, self-closing <br/> and <img …/>, and &amp; &lt; &gt; escaped \
(especially inside <code>).
"""

MATH_RULES = {
    "mathml": """\
- Write every formula, inline or display, as presentation MathML: \
<math xmlns="http://www.w3.org/1998/Math/MathML" alttext="LaTeX of the formula">…</math>. \
Add display="block" for display formulas (on their own line). Use <mi>, <mn>, <mo>, <mfrac>, \
<msup>, <msub>, <msubsup>, <msqrt>, <mroot>, <munderover>, <mrow>, <mtable>, etc. \
Keep the original numbering of equations, e.g. "(3.2)", as text next to the formula.
- Simple variables inside prose (a single letter such as x or n) may also be MathML <math><mi>x</mi></math>.""",
    "image": """\
- Display formulas (on their own line) are available as "formula" assets: insert them with \
<img class="formula" src="ID" alt="LaTeX of the formula"/> inside a <p class="formula">. \
Keep the original equation numbering as text next to it.
- Inline formulas inside prose: write them as HTML text, with <i> for variables and \
<sub>/<sup> for indices and exponents (e.g. <i>x</i><sup>2</sup>).""",
}


def layout_system(math_mode: str = "mathml") -> str:
    return (LAYOUT_SYSTEM
            .replace("{continues}", CONTINUES_MARK)
            .replace("{box_open}", BOX_OPEN).replace("{box_close}", BOX_CLOSE)
            .replace("{math_open}", MATH_OPEN).replace("{math_close}", MATH_CLOSE)
            .replace("{math_rules}", MATH_RULES[math_mode]))


def _pages_text(pages: list[Page]) -> str:
    parts = []
    for p in pages:
        note = " (no extractable text: transcribe it from the PDF)" if p.no_text else ""
        if p.columns > 1:
            note += f" ({p.columns} columns, text given in reading order)"
        parts.append(f"=== Page {p.number}{note} ===\n{p.text}")
    return "\n\n".join(parts)


def _asset_list(assets: list[Asset]) -> str:
    if not assets:
        return "(none)"
    return "\n".join(f"- {a.id}: {a.kind} on page {a.page}, {a.width}x{a.height}px" for a in assets)


def layout(
    engine: Engine,
    profile: Profile,
    chapter: Chapter,
    pages: list[Page],
    pdf_bytes: bytes | None,
    previous_end: str,
    chapter_start: bool,
    math_mode: str = "mathml",
) -> str:
    assets = [a for p in pages for a in p.assets]
    text = f"""\
## Book
{profile.summary()}

## Current chapter
"{chapter.title}" (pages {chapter.start}–{chapter.end}). \
This chunk is pages {pages[0].number}–{pages[-1].number}. \
{"It is the start of the chapter: open with its <h1>." if chapter_start else "It continues the chapter: do not repeat the <h1>."}

## End of the previous chunk
{previous_end[-1500:] if previous_end else "(none: this is the first chunk of the chapter)"}

## Available graphic assets
{_asset_list(assets)}

## Extracted, annotated text
('{CODE_MARK}' = line in a monospaced font; [Hn] = line in n pt type, likely a heading; **x** = bold;
{MATH_OPEN}…{MATH_CLOSE} = math font; {BOX_OPEN}…{BOX_CLOSE} = inside a drawn frame)

{_pages_text(pages)}
"""
    content: list[dict] = []
    if pdf_bytes:
        content.append(pdf_block(pdf_bytes))
    content.append({"type": "text", "text": text})
    return clean_output(engine.ask(layout_system(math_mode), content))


def clean_output(xhtml: str) -> str:
    xhtml = xhtml.strip()
    if xhtml.startswith("```"):
        xhtml = xhtml.split("\n", 1)[1] if "\n" in xhtml else ""
        xhtml = xhtml.rsplit("```", 1)[0]
    return xhtml.strip()


# ---------------------------------------------------------------------------
# 3. Reviewer
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM = """\
You are the Reviewer agent of a PDF-to-EPUB book converter. Another agent converted some \
pages to XHTML and the automatic checks found problems. Your task is to return the \
corrected, complete XHTML fragment.

- Compare the XHTML with the original text and the PDF: restore everything that is missing \
(paragraphs, code lines, table cells, notes) in its correct position.
- Code must be identical to the original, character by character, inside \
<pre><code class="language-XXX">.
- Do not summarize or rewrite what is already correct; fix only what is needed.
- Keep the book's original language; never translate.
- Keep formulas in the same form as the rest of the fragment (MathML or formula images).
- Keep the {continues} mark at the start if it was there.
- Answer only with the corrected, well-formed XHTML fragment, without Markdown.
""".replace("{continues}", CONTINUES_MARK)


def review(
    engine: Engine,
    profile: Profile,
    pages: list[Page],
    pdf_bytes: bytes | None,
    xhtml: str,
    problems: list[str],
) -> str:
    text = f"""\
## Book
{profile.summary()}

## Problems found
{chr(10).join('- ' + p for p in problems)}

## Original annotated text of pages {pages[0].number}–{pages[-1].number}
('{CODE_MARK}' = line in a monospaced font)

{_pages_text(pages)}

## XHTML to fix
{xhtml}
"""
    content: list[dict] = []
    if pdf_bytes:
        content.append(pdf_block(pdf_bytes))
    content.append({"type": "text", "text": text})
    return clean_output(engine.ask(REVIEWER_SYSTEM, content))
