# pdftoepub

**English** · [Español](README.es.md)

**Convert PDF books into clean, coherent EPUBs that stay faithful to the original, using AI agents (Claude).**

Built especially for technical books: programming, Linux, system administration,
networking, command-line tools… Code is preserved character by character, in readable
blocks with syntax highlighting, and the text is reflowed to read comfortably on any
e-book reader.

> [!IMPORTANT]
> **You need Claude Code installed and logged in on your machine.**
> The AI agents run through your local `claude` command (non-interactive mode
> `claude -p`), so the program uses your Claude Code account (Pro / Max plan or a
> Claude Console account). See [Requirements](#requirements).

---

## Contents

- [Why another converter](#why-another-converter)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Options](#options)
- [Interface language](#interface-language)
- [How it works](#how-it-works)
- [Example output](#example-output)
- [Resuming and fixing a conversion](#resuming-and-fixing-a-conversion)
- [Usage and costs](#usage-and-costs)
- [Alternative engine: Anthropic API](#alternative-engine-anthropic-api)
- [Project structure](#project-structure)
- [Development and tests](#development-and-tests)
- [Math, diagrams and complex layouts](#math-diagrams-and-complex-layouts)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Privacy and copyright](#privacy-and-copyright)
- [License](#license)

---

## Why another converter

Classic converters (Calibre, pandoc, etc.) treat a PDF as letters drawn at fixed
positions. With technical books the result is usually poor:

- Code blocks become paragraphs, lose their indentation or get mixed with the text.
- Words hyphenated at the end of a line stay broken (`termi- nal`).
- Running headers, footers and page numbers show up in the middle of the text.
- Tables fall apart and long commands get cut off.

`pdftoepub` combines precise PDF extraction with AI agents that **understand** what they
are reading (this is a heading, this is a terminal session, this is a table of options),
plus automatic checks that make sure no content is lost.

## Features

- 🧠 **Three specialized agents**: Analyst (structure), Layout (conversion) and Reviewer (fixes).
- 💻 **Code faithful to the original**: monospaced text detected in the PDF, indentation rebuilt,
  straight quotes, `--options` intact, language identified (`bash`, `console`, `python`, `c`, `yaml`…)
  and syntax highlighting.
- 👁️ **Vision**: the model sees the real page as well as the text, so it rebuilds tables, note boxes
  and figures correctly. Scanned PDFs are transcribed from the image.
- ✅ **Automatic verification**: every chunk is checked (word coverage, code lines, valid XHTML);
  if something is missing, the Reviewer agent fixes it.
- ➗ **Math**: formulas become MathML (sharp and scalable) or, for readers without MathML
  such as Kindle, images with their LaTeX as alt text.
- 📐 **Vector diagrams kept as SVG**: sharp at any zoom level, with PNG as a fallback.
- 📰 **Complex layouts**: multi-column pages are read in the right order, and framed
  sidebars, notes and warnings are kept together as boxes.
- 🔗 **Continuity across pages**: paragraphs, lists, tables and code blocks split across pages are merged.
- 📚 **Complete EPUB 3**: cover, metadata, navigable table of contents with chapters and sections,
  images and diagrams, and a stylesheet designed for e-readers (long code wraps instead of being cut).
- ♻️ **Resumable**: everything is saved in a work folder; if interrupted, it continues where it stopped.
- 🌍 **English or Spanish interface**; the book always keeps its original language.
- ⚙️ **Tunable**: model, effort level, chunk size, parallelism, page range…

## Requirements

| Requirement | Details |
|---|---|
| **Claude Code** | Installed and logged in. The `claude` command must work in your terminal. Requires a Claude Pro/Max plan or a Claude Console account. [Install Claude Code](https://claude.com/claude-code) |
| **Python 3.13+** | |
| **[uv](https://docs.astral.sh/uv/)** | Python project manager (recommended). |
| OS | Linux, macOS or Windows (WSL). |

Check that Claude Code is ready:

```bash
claude --version          # should print the version
claude -p "say hello"     # should answer without asking you to log in
```

If it's your first time, run `claude` once to log in.

## Installation

```bash
git clone https://github.com/jmloz/pdftoepub.git
cd pdftoepub
uv sync
```

Optionally, install it as a global command:

```bash
uv tool install .
pdftoepub --help
```

## Quick start

```bash
# 1. See how much it will use
uv run pdftoepub book.pdf --estimate

# 2. Try the first pages and check the result
uv run pdftoepub book.pdf --pages 1-30 -o test.epub

# 3. Full conversion (creates book.epub next to the PDF)
uv run pdftoepub book.pdf
```

During the conversion you will see each agent's progress:

```
· Extracting text, code and images from book.pdf…
  412 pages, 10pt body text, 57 images/diagrams
· Analyst agent (Claude Code): studying the book structure…
· «Practical Linux Guide» — 18 chapters (Operating systems / Linux)
· Layout agent converting chapters…
  · Reviewer agent fixing pages 37-42: 4 of 61 code lines are missing or altered…
  ✓ Chapter 1: Introduction to the terminal
  ...
· Assembling EPUB…
✔ EPUB created: book.epub
```

More examples:

```bash
# Faster and lighter: no page images
uv run pdftoepub book.pdf --no-vision

# Maximum quality for a hard book (complex tables, lots of code)
uv run pdftoepub book.pdf --model claude-opus-5-5 --effort xhigh --pages-per-chunk 4

# Basic conversion without AI (does not need Claude Code; useful for testing)
uv run pdftoepub book.pdf --no-ai

# Interface in Spanish
uv run pdftoepub book.pdf --lang es
```

## Options

| Option | Default | Description |
|---|---|---|
| `-o, --output` | `<pdf>.epub` | Path of the generated EPUB. |
| `--engine` | `claude-code` | `claude-code` uses your local Claude Code; `api` uses the Anthropic API ([see below](#alternative-engine-anthropic-api)). |
| `--model` | `claude-sonnet-5-5` | Claude model used by the agents. `claude-opus-5-5` gives the best quality but uses about twice as much. |
| `--effort` | `high` | Reasoning depth: `low`, `medium`, `high`, `xhigh`, `max`. |
| `--pages-per-chunk` | `6` | Pages the Layout agent processes per call. Fewer pages = more precision and more calls. |
| `--parallel` | `3` | Chapters converted at the same time. |
| `--reviews` | `2` | Maximum Reviewer rounds per chunk. |
| `--pages` | all | Convert only a page range, e.g. `1-40`. |
| `--no-vision` | — | Don't send page images to the model (lower usage; worse with tables, figures and scans). |
| `--no-highlight` | — | Code without syntax colors. |
| `--math` | `mathml` | Formulas as `mathml` (sharp, scalable, searchable) or `image` (for readers without MathML, e.g. Kindle). |
| `--diagrams` | `svg` | Vector diagrams as `svg` (sharp at any size) or `png`. |
| `--no-ai` | — | Basic deterministic conversion, no agents. |
| `--estimate` | — | Only estimate the tokens the conversion will use. |
| `--status` | — | Show the progress of a running or interrupted conversion and exit. |
| `--lang` | auto | Interface language: `en` or `es` ([see below](#interface-language)). |

## Interface language

The program's messages and help are available in **English** and **Spanish**. The language
is chosen in this order:

1. The `--lang` option: `pdftoepub book.pdf --lang es`
2. The `PDFTOEPUB_LANG` environment variable: `export PDFTOEPUB_LANG=es`
3. Your system locale (`LANG`, `LC_ALL`…): if it starts with `es`, Spanish is used.
4. Otherwise, English.

The interface language does **not** affect the book: the EPUB always keeps the
language of the original PDF.

## How it works

```
          ┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐
 PDF ───► │  Extractor   │──►│  Analyst agent   │──►│    Layout agent    │──┐
          │   (no AI)    │   │   (structure)    │   │  (chunk by chunk)  │  │
          └──────────────┘   └──────────────────┘   └────────────────────┘  │
                                                              ▲             ▼
                                                    ┌─────────┴───┐  ┌─────────────┐
                                                    │  Reviewer   │◄─│  Verifier   │
                                                    │    agent    │  │   (no AI)   │
                                                    └─────────────┘  └──────┬──────┘
                                                                            ▼
                                                    ┌──────────────┐
                                           EPUB ◄───│  Assembler   │
                                                    │   (no AI)    │
                                                    └──────────────┘
```

1. **Extractor** (`extractor.py`, no AI). Reads the PDF with PyMuPDF and produces *annotated*
   text in **reading order**:
   - multi-column pages are detected: full-width elements (titles, figures) split the page
     into bands, and inside each band columns that sit side by side are read left to right;
   - text inside a drawn frame is kept together and marked as a box (sidebar, note, warning…);
   - lines in a monospaced font are marked as code and their indentation is rebuilt from their
     horizontal position (per column);
   - text in a math font (Computer Modern math, STIX, Cambria Math, Symbol…) is marked as math;
   - lines larger than the body text are marked as likely headings;
   - repeated running headers, footers and page numbers are removed;
   - images are exported, and vector diagrams (nearby strokes merged into one figure) are
     exported as SVG, or PNG if the SVG would be too heavy.

2. **Analyst agent**. Studies the PDF bookmarks, heading candidates and first pages, and
   produces the book's *profile*: title, authors, language, subject, code languages present,
   typographic conventions and the chapter split. Saved to `profile.json`.

3. **Layout agent**. For each chunk of pages it receives the image of those pages (PDF), the
   annotated text, the book profile and the end of the previous chunk. It returns semantic
   XHTML: full paragraphs, rejoined hyphenated words, `<pre><code class="language-…">` for
   code, tables, lists, notes, keys, figures with alt text, footnotes and formulas in MathML
   (reading them from the page image, since math extracted from PDFs is often garbled). Its main rule is
   **fidelity**: it restructures, it never summarizes or rewrites.

4. **Verifier** (`verifier.py`, no AI). Checks every chunk:
   - the XHTML is well-formed;
   - at least 93% of the original's words are present;
   - every code line of the original is present;
   - referenced images exist.

5. **Reviewer agent**. Steps in only when the verifier finds problems: it receives the
   concrete list of failures and returns the fixed chunk. Repeats up to `--reviews` times.

6. **Assembler** (`assembler.py`, no AI). Merges each chapter's chunks (including paragraphs
   and code split across pages), highlights code with Pygments, numbers the sections,
   builds the navigable table of contents and the cover, and writes the EPUB 3.

## Example output

A PDF page with a paragraph broken across lines, a command and a script:

```text
This chapter explains how to use the Linux termi-
nal to manage files efficiently.
1.1 Listing files
The ls command lists the contents of a directory:
$ ls -la /etc | grep --color=auto conf
for f in *.txt; do
    echo "$f" >> list.log
done
```

XHTML produced by the agents:

```html
<h1>Chapter 1. Basic commands</h1>
<p>This chapter explains how to use the Linux terminal to manage files efficiently.</p>
<h2>1.1 Listing files</h2>
<p>The <code>ls</code> command lists the contents of a directory:</p>
<pre><code class="language-console">$ ls -la /etc | grep --color=auto conf</code></pre>
<pre><code class="language-bash">for f in *.txt; do
    echo "$f" &gt;&gt; list.log
done</code></pre>
```

The broken word is rejoined, the terminal command (`console`) and the script (`bash`)
are separated and identified, and the code indentation is preserved.

## Resuming and fixing a conversion

While converting, every finished chunk prints the overall progress and an estimate of the
time left:

```
  [45/254 chunks · 17% · ~1h 12m left] pages 101-106
```

From another terminal (or after an interruption) you can check it at any time:

```bash
uv run pdftoepub book.pdf --status
# 45/254 chunks · 17% · ~1h 12m left · last update 1m ago
```

All intermediate work is saved in a folder next to the EPUB (`book.work/`):

```
book.work/
├── profile.json    # book analysis (metadata and chapters)
├── progress.json   # chunks done / total, used by --status
├── chunks/         # XHTML of every converted chunk
├── images/         # extracted images and diagrams
└── cover.png
```

- **If it is interrupted** (Ctrl+C, network failure, usage limit reached), run the same
  command again: only the missing chunks are processed.
- **If a chapter is split wrongly** or the title/author is wrong, edit `profile.json`,
  delete the `chunks/` folder and run again. The analysis is not repeated.
- **To regenerate a specific chunk**, delete its file in `chunks/` and run again.
- **To start from scratch**, delete the `book.work/` folder.

## Usage and costs

With the default engine (Claude Code), what you pay depends on how Claude Code is logged in
on your machine:

| How you logged in to Claude Code | What `pdftoepub` uses |
|---|---|
| **Claude Pro / Max** plan | Your **plan's usage allowance**. No extra money is charged (unless you enabled paid extra usage). |
| **Claude Console** (API) account, or `ANTHROPIC_API_KEY` set in the environment | **Real money**, per token, at API prices. |

When it finishes, the program shows the tokens used and their **equivalent** API price.
It's only a reference: on a Pro/Max plan you are not charged that amount.

> [!WARNING]
> If the `ANTHROPIC_API_KEY` variable is set in your terminal, Claude Code uses it instead
> of your plan and bills per token. To use your plan, unset it before running the program:
> `unset ANTHROPIC_API_KEY`.

As a rough guide, each page uses about 2,000–4,000 input tokens (text + page image) and an
output similar to the page's text. A 400-page book can use a significant part of a Pro
plan's allowance; on Max plans, or with `--no-vision` and `--effort medium`, usage is much
more manageable. The default model is Sonnet; `--model claude-opus-5-5` uses about twice as much.

Tips to save usage:

- Use `--estimate` before starting and `--pages` to try a few pages first.
- `--no-vision` greatly reduces input; use it if the PDF has clean text and few tables.
- `--effort medium` for simple books; keep `--model claude-opus-5-5` for the hardest ones.
- If you hit the usage limit, wait for it to reset and run again: the conversion resumes.

## Alternative engine: Anthropic API

If you prefer not to use Claude Code (for example, on a server), you can use the Anthropic
API with a key from [Claude Console](https://console.anthropic.com/). In that case you are
billed per token.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pdftoepub book.pdf --engine api
```

With this engine, if the model refuses a fragment (which can happen with computer security
books), the API automatically retries it with a fallback model.

## Project structure

```
pdftoepub/
├── src/pdftoepub/
│   ├── __init__.py     # command-line interface
│   ├── pipeline.py     # coordinates the flow, cache and parallelism
│   ├── extractor.py    # PDF reading (reading order, annotated text, images, SVG diagrams, math)
│   ├── agents.py       # prompts and logic of the Analyst, Layout and Reviewer agents
│   ├── verifier.py     # automatic quality checks
│   ├── assembler.py    # chunk merging, highlighting and EPUB generation
│   ├── llm.py          # AI engines: local Claude Code and Anthropic API
│   └── i18n.py         # interface messages in English and Spanish
├── tests/
│   └── test_pipeline.py
├── pyproject.toml
├── README.md           # English documentation
└── README.es.md        # Spanish documentation
```

## Development and tests

```bash
uv sync
uv run pytest
```

The tests use a fake Claude and a fake `claude` executable, so they **use no plan
allowance** and need no network. They cover the full flow (analysis → layout → review →
EPUB), caching/resuming, merging chunks split across pages, the verifier, the Claude Code
engine, the interface languages, multi-column reading order, framed boxes, SVG diagrams
and MathML.

For a quick real test with Claude Code: `uv run pdftoepub book.pdf --pages 1-5`.

To add a new interface message, add its key with both `en` and `es` texts to
`MESSAGES` in `i18n.py` (a test checks that every message has both languages).

## Math, diagrams and complex layouts

**Math.** By default formulas are written as MathML, the EPUB 3 standard for math: they
stay sharp at any size, follow the reader's font settings and keep their LaTeX source as
`alttext` for screen readers. Apple Books, Kobo, Thorium and most modern readers display
MathML. **Kindle** and some older readers don't: for them use `--math image`, which turns
display formulas into images (SVG) with their LaTeX as alt text and writes simple inline
math as text with italics and sub/superscripts.

**Diagrams.** Vector drawings in the PDF (flowcharts, network diagrams, plots) are exported
as SVG so they stay sharp when zoomed. Use `--diagrams png` if your reader has trouble
with SVG images.

**Complex layouts.** Multi-column pages are reordered into reading order before the agents
see them, and the Layout agent also checks the order against the page image. Text inside
drawn frames is kept together and turned into `<aside>` boxes (or code listings / tables
when that's what the frame holds).

Changing `--math` regenerates the chunks (it's part of the cache key); `--diagrams` only
changes the exported images.

## Limitations

- Math is detected by font: formulas set in a regular text font are not marked as math,
  although the Layout agent often still recognizes them from the page image.
- **Very unusual layouts** (magazines with text flowing around images, rotated text) may
  still require reviewing `profile.json` or lowering `--pages-per-chunk`.
- The original's **page numbers** disappear; references like "see page 42" are kept as text.
- Password-protected PDFs are not supported.
- The book keeps its original language (no translation).

## Troubleshooting

| Problem | Solution |
|---|---|
| `The claude command was not found` | Install Claude Code and make sure `claude` is on your `PATH`. |
| `Claude Code returned an error` / asks you to log in | Run `claude` in a terminal and log in; try `claude -p "hello"`. |
| Stops because of the usage limit | Wait for the allowance to reset and run the same command again: it resumes. |
| Chapters split wrongly | Edit `book.work/profile.json`, delete `chunks/` and run again. |
| A chunk looks wrong | Delete its file in `book.work/chunks/` and run again, maybe with `--pages-per-chunk 3`. |
| `warnings remain after N reviews` | The chunk is included anyway; check it in the EPUB or regenerate it with more `--reviews`. |
| Scanned pages are empty | Don't use `--no-vision`: those pages are read from the image. |
| Formulas don't show on my reader (e.g. Kindle) | Convert again with `--math image`. |
| Diagrams don't show | Convert again with `--diagrams png`. |

## Privacy and copyright

- The PDF's content (text and page images) is sent to Anthropic so Claude can process it,
  just like any Claude Code conversation. Don't convert documents you can't share with an
  external service.
- Only use this tool with books you have the right to convert (for example, to read a book
  you bought on your own e-reader). Don't distribute EPUBs generated from copyrighted works.

## License

Distributed under the [MIT](LICENSE) license.
