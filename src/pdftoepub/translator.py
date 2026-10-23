"""EPUB translation with AI agents.

Works on any EPUB (the ones pdftoepub generates or others). The EPUB is rewritten in
place: styles, images and fonts are untouched; only the text is translated.

1. Glossary agent  -> source language, translated title and a terminology glossary,
                      so the whole book uses the same terms.
2. Translator agent -> translates each chunk of XHTML keeping its exact structure.

Code blocks (<pre>), MathML and SVG never reach the model: they are swapped for
placeholders before translating and restored afterwards. Inline code stays in place for
context, and the checks make sure it comes back unchanged.
"""

from __future__ import annotations

import copy
import json
import posixpath
import re
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .agents import clean_output
from .i18n import t
from .llm import Engine, RefusedResponse, TruncatedResponse
from .pipeline import Progress, log, report_usage
from .verifier import XHTML_NS, _html_entities

NS = f"{{{XHTML_NS}}}"
EPUB_NS = "http://www.idpf.org/2007/ops"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

CHUNK_CHARS = 9000  # serialized XHTML per translation call
MASKED = {"pre", "math", "svg", "script", "style"}  # never sent to the model
LITERAL = {"code", "kbd", "samp", "var", "tt"}  # inline code: must come back unchanged
STRUCTURAL = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "dl", "dt", "dd", "table", "tr",
              "td", "th", "figure", "img", "a", "aside", "blockquote", "section", "div", "pre", "math", "svg"}

LANGUAGE_NAMES = {
    "es": "Spanish", "en": "English", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "ca": "Catalan", "gl": "Galician", "eu": "Basque", "nl": "Dutch", "pl": "Polish", "ru": "Russian",
    "uk": "Ukrainian", "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "ar": "Arabic", "tr": "Turkish",
}


def language_name(code: str) -> str:
    base = code.lower().split("-")[0]
    return f"{LANGUAGE_NAMES[base]} ({code})" if base in LANGUAGE_NAMES else code


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

GLOSSARY_SYSTEM = """\
You are the Glossary agent of an e-book translator. You receive the metadata and a sample \
of a book and prepare the translation into {target}.

- source_language: BCP 47 code of the book's language.
- title: the book title translated into {target} (keep product names and acronyms).
- style: one or two sentences fixing the voice for the whole book in {target}: how the \
reader is addressed (e.g. in Spanish "tú" or "usted"; follow the usual convention for \
technical books in {target}) and the tone. Every chapter will follow it.
- glossary: 20–60 technical terms that appear in the sample, with the translation every \
chapter must use. In technical books many terms are usually kept in English in {target} \
(e.g. thread, framework, commit, array when that is the common usage among professionals): \
in that case the translation is the same term. Prefer the terminology professionals \
actually use in {target}, not literal translations.
"""

GLOSSARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_language", "title", "style", "glossary"],
    "properties": {
        "source_language": {"type": "string"},
        "title": {"type": "string"},
        "style": {"type": "string"},
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["term", "translation"],
                "properties": {"term": {"type": "string"}, "translation": {"type": "string"}},
            },
        },
    },
}

TRANSLATOR_SYSTEM = """\
You are the Translator agent of an e-book translator, specialized in technical books. You \
translate XHTML fragments of a book into {target}.

Rules:
- Translate all human-readable text: paragraphs, headings, list items, table cells, \
captions, notes, and the alt and title attributes. Natural, fluent {target}, in the \
register of a good technical book. Do not summarize, omit or add anything.
- Use the glossary terms exactly as given and follow the style (how the reader is addressed, tone).
- Keep the markup EXACTLY: the same elements in the same order and nesting, with the same \
attributes (id, class, href, src, epub:type…). Only text and the alt/title values change.
- Never translate or alter: the content of <code>, <kbd>, <samp>, <var> and <tt>; URLs, \
file names, commands, options, identifiers and code that appears inside the text; \
elements with a data-keep attribute (placeholders for code blocks and formulas: copy them as they are).
- Proper names, product names and acronyms stay as they are.
- Answer only with the translated XHTML fragment (no Markdown, no ```), well-formed, with \
the same number of top-level elements as the input.
"""

LABELS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["translations"],
    "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
}


@dataclass
class Glossary:
    source_language: str
    title: str
    terms: list[dict] = field(default_factory=list)
    style: str = ""

    def text(self) -> str:
        terms = "\n".join(f"- {g['term']} → {g['translation']}" for g in self.terms) or "(none)"
        return f"Style: {self.style or '(natural, consistent)'}\n{terms}"


# ---------------------------------------------------------------------------
# EPUB structure
# ---------------------------------------------------------------------------

@dataclass
class Book:
    files: dict[str, bytes]  # every file of the EPUB, by path inside the zip
    order: list[str]  # original zip order
    opf_path: str
    documents: list[str]  # XHTML documents to translate, in spine order first
    ncx: str | None


def _xml(data: bytes) -> etree._ElementTree:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True)
    text = _html_entities(data.decode("utf-8", errors="replace"))
    text = re.sub(r"^<\?xml[^>]*\?>", "", text.lstrip("﻿"))
    return etree.ElementTree(etree.fromstring(text.encode(), parser))


def _serialize(tree: etree._ElementTree, original: bytes) -> bytes:
    doctype = re.search(rb"<!DOCTYPE[^>]*>", original[:2000])
    body = etree.tostring(tree.getroot(), encoding="unicode")
    head = '<?xml version="1.0" encoding="utf-8"?>\n'
    if doctype:
        head += doctype.group(0).decode() + "\n"
    return (head + body).encode()


def read_book(path: Path) -> Book:
    with zipfile.ZipFile(path) as z:
        order = z.namelist()
        files = {name: z.read(name) for name in order}
    container = _xml(files["META-INF/container.xml"])
    opf_path = container.getroot().find(".//{*}rootfile").get("full-path")
    opf = _xml(files[opf_path]).getroot()
    base = posixpath.dirname(opf_path)

    manifest = {}
    for item in opf.iter(f"{{{OPF_NS}}}item"):
        manifest[item.get("id")] = (posixpath.normpath(posixpath.join(base, item.get("href"))), item.get("media-type"))
    spine = [manifest[i.get("idref")][0] for i in opf.iter(f"{{{OPF_NS}}}itemref") if i.get("idref") in manifest]
    xhtml = [p for p, media in manifest.values() if media == "application/xhtml+xml" and p in files]
    documents = [p for p in spine if p in xhtml] + [p for p in xhtml if p not in spine]
    ncx = next((p for p, media in manifest.values() if media == "application/x-dtbncx+xml" and p in files), None)
    return Book(files, order, opf_path, documents, ncx)


def write_book(book: Book, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w") as z:
        # The mimetype must be the first entry, stored without compression.
        if "mimetype" in book.files:
            z.writestr(zipfile.ZipInfo("mimetype"), book.files["mimetype"], compress_type=zipfile.ZIP_STORED)
        for name in book.order:
            if name != "mimetype":
                z.writestr(name, book.files[name], compress_type=zipfile.ZIP_DEFLATED)


def _prose(el: etree._Element) -> list[str]:
    """Text of an element, skipping code blocks, math and SVG."""
    if _local(el.tag) in MASKED:
        return [el.tail or ""]
    parts = [el.text or ""]
    for child in el:
        parts += _prose(child)
    return parts + [el.tail or ""]


def plain_text(book: Book, limit: int = 20000) -> str:
    """Prose of the book (no code), up to `limit` characters."""
    parts, size = [], 0
    for doc in book.documents:
        body = _xml(book.files[doc]).getroot().find(f"{NS}body")
        if body is None:
            continue
        text = re.sub(r"\s+", " ", " ".join(_prose(body))).strip()
        parts.append(text)
        size += len(text)
        if size > limit:
            break
    return " ".join(parts)[:limit]


# ---------------------------------------------------------------------------
# Chunking, masking and checks
# ---------------------------------------------------------------------------

def _size(el: etree._Element) -> int:
    return len(etree.tostring(el, encoding="unicode"))


def chunk_units(body: etree._Element) -> list[list[etree._Element]]:
    """Group the body's elements into chunks of consecutive siblings of ≤ CHUNK_CHARS.

    Containers larger than a chunk (a <section> or <div> holding a whole chapter) are
    descended into, so each chunk is a run of siblings that can be replaced one-to-one.
    """
    chunks: list[list[etree._Element]] = []

    def walk(parent: etree._Element) -> None:
        current, size = [], 0
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            s = _size(child)
            if s > CHUNK_CHARS and len(child) and _local(child.tag) not in MASKED | {"table", "p"}:
                if current:
                    chunks.append(current)
                    current, size = [], 0
                walk(child)
                continue
            if current and size + s > CHUNK_CHARS:
                chunks.append(current)
                current, size = [], 0
            current.append(child)
            size += s
        if current:
            chunks.append(current)

    walk(body)
    return chunks


def mask(units: list[etree._Element]) -> tuple[list[etree._Element], dict[str, etree._Element]]:
    """Copies of the units with code blocks, math and SVG replaced by placeholders."""
    kept: dict[str, etree._Element] = {}
    masked = [copy.deepcopy(u) for u in units]

    def replace(el: etree._Element) -> etree._Element:
        key = str(len(kept))
        kept[key] = copy.deepcopy(el)
        tag = el.tag if el.tag == f"{NS}pre" else f"{NS}span"
        placeholder = etree.Element(tag, {"data-keep": key})
        placeholder.tail = el.tail
        return placeholder

    for i, unit in enumerate(masked):
        if _local(unit.tag) in MASKED:
            masked[i] = replace(unit)
            continue
        for el in list(unit.iter()):
            if _local(el.tag) in MASKED and el.getparent() is not None:
                el.getparent().replace(el, replace(el))
    return masked, kept


def to_fragment(units: list[etree._Element]) -> str:
    text = "\n".join(etree.tostring(u, encoding="unicode", with_tail=False) for u in units)
    return text.replace(f' xmlns="{XHTML_NS}"', "").replace(f' xmlns:epub="{EPUB_NS}"', "")


def from_fragment(fragment: str) -> list[etree._Element]:
    wrapper = f'<div xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}">{_html_entities(fragment)}</div>'
    return [el for el in etree.fromstring(wrapper.encode()) if isinstance(el.tag, str)]


def _tags(units: list[etree._Element]) -> Counter:
    return Counter(_local(el.tag) for u in units for el in u.iter() if _local(el.tag) in STRUCTURAL)


def _literals(units: list[etree._Element]) -> Counter:
    return Counter("".join(el.itertext()) for u in units for el in u.iter() if _local(el.tag) in LITERAL)


def _links(units: list[etree._Element]) -> Counter:
    return Counter((k, v) for u in units for el in u.iter() for k, v in el.attrib.items()
                   if k in ("href", "src", "id", "data-keep"))


def _text(units: list[etree._Element]) -> str:
    return re.sub(r"\s+", " ", " ".join(t for u in units for t in u.itertext())).strip()


def check(source: list[etree._Element], translated: list[etree._Element]) -> list[str]:
    problems = []
    if [_local(u.tag) for u in source] != [_local(u.tag) for u in translated]:
        problems.append(f"The top-level elements changed: expected {[_local(u.tag) for u in source]}, "
                        f"got {[_local(u.tag) for u in translated]}.")
    if _tags(source) != _tags(translated):
        diff = (_tags(source) - _tags(translated)) + (_tags(translated) - _tags(source))
        problems.append(f"The structure changed (element counts differ for: {', '.join(sorted(diff))}).")
    lost = _literals(source) - _literals(translated)
    if lost:
        problems.append("Inline code was altered or lost: " + ", ".join(repr(x) for x in list(lost)[:10]))
    if _links(source) != _links(translated):
        problems.append("Attributes id/href/src/data-keep changed; keep them exactly as in the input.")
    src, out = _text(source), _text(translated)
    if src and not 0.4 <= len(out) / len(src) <= 2.5:
        problems.append(f"The translation is {len(out) / len(src):.1f}× the original length: something was lost or added.")
    if len(re.findall(r"[^\W\d_]", src)) > 200 and src == out:
        problems.append("The text was not translated.")
    return problems


def unmask(units: list[etree._Element], kept: dict[str, etree._Element]) -> list[etree._Element]:
    restored = []
    for unit in units:
        if unit.get("data-keep") in kept:
            original = copy.deepcopy(kept[unit.get("data-keep")])
            original.tail = unit.tail
            restored.append(original)
            continue
        for el in list(unit.iter()):
            key = el.get("data-keep")
            if key in kept and el.getparent() is not None:
                original = copy.deepcopy(kept[key])
                original.tail = el.tail
                el.getparent().replace(el, original)
        restored.append(unit)
    return restored


def _has_text(units: list[etree._Element]) -> bool:
    return bool(re.search(r"[^\W\d_]", _text(units))) or any(
        el.get("alt") or el.get("title") for u in units for el in u.iter())


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------

class Translator:
    def __init__(self, source: Path, output: Path, target: str, engine: Engine, parallel: int = 3, reviews: int = 2):
        self.source = source
        self.output = output
        self.target = target
        self.engine = engine
        self.parallel = parallel
        self.reviews = reviews
        self.work_dir = output.with_suffix(".work")
        self.chunk_dir = self.work_dir / "chunks"
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self.progress: Progress | None = None
        # Original heading text -> its translation in the body, so the table of contents
        # uses exactly the same wording as the chapters.
        self.headings: dict[str, str] = {}

    def system(self, template: str) -> str:
        return template.replace("{target}", language_name(self.target))

    def glossary(self, book: Book) -> Glossary:
        path = self.work_dir / "glossary.json"
        if path.exists():
            return Glossary(**json.loads(path.read_text()))
        log(t("tr.glossary", engine=self.engine.name))
        opf = _xml(book.files[book.opf_path]).getroot()
        title = opf.findtext(f".//{{{DC_NS}}}title") or ""
        language = opf.findtext(f".//{{{DC_NS}}}language") or ""
        content = f"Title: {title}\nDeclared language: {language}\n\n## Sample\n{plain_text(book)}"
        data = self.engine.ask_json(self.system(GLOSSARY_SYSTEM), content, GLOSSARY_SCHEMA)
        glossary = Glossary(data["source_language"], data["title"], data["glossary"], data["style"])
        path.write_text(json.dumps(glossary.__dict__, ensure_ascii=False, indent=2))
        return glossary

    def _cache(self, doc: str, n: int) -> Path:
        safe = re.sub(r"[^\w.-]", "_", doc)
        return self.chunk_dir / f"{safe}.{n:04d}.html"

    def _translate_chunk(self, glossary: Glossary, doc: str, n: int, units: list[etree._Element]) -> list[etree._Element] | None:
        masked, kept = mask(units)
        cache = self._cache(doc, n)
        if cache.exists():
            return unmask(from_fragment(cache.read_text()), kept)
        if not _has_text(masked):
            return None  # only code or images without alt text: nothing to translate

        source_fragment = to_fragment(masked)
        prompt = (f"## Glossary\n{glossary.text()}\n\n## Book language\n{glossary.source_language}\n\n"
                  f"## XHTML to translate into {language_name(self.target)}\n{source_fragment}")
        problems: list[str] = []
        for attempt in range(self.reviews + 1):
            content = prompt if not problems else (
                prompt + "\n\n## Your previous answer had these problems; fix them\n"
                + "\n".join(f"- {p}" for p in problems))
            try:
                answer = clean_output(self.engine.ask(self.system(TRANSLATOR_SYSTEM), content))
                translated = from_fragment(answer)
                problems = check(masked, translated)
            except etree.XMLSyntaxError as e:
                problems = [f"Malformed XHTML: {e}"]
            except (RefusedResponse, TruncatedResponse) as e:
                problems = [type(e).__name__]
                break
            if not problems:
                cache.write_text(to_fragment(translated))
                return unmask(translated, kept)
            if attempt < self.reviews:
                log(t("tr.retry", doc=doc, n=n, problem=problems[0][:120]))
        log(t("tr.kept", doc=doc, n=n, problem=problems[0][:120] if problems else ""))
        cache.write_text(source_fragment)  # don't retry on resume: the original stays
        return None

    def _translate_document(self, glossary: Glossary, book: Book, doc: str) -> None:
        tree = _xml(book.files[doc])
        root = tree.getroot()
        body = root.find(f"{NS}body")
        if body is None:
            return
        for n, units in enumerate(chunk_units(body)):
            cached = self._cache(doc, n).exists()
            translated = self._translate_chunk(glossary, doc, n, units)
            if translated is not None:
                parent = units[0].getparent()
                index = parent.index(units[0])
                tail = units[-1].tail
                for u in units:
                    parent.remove(u)
                self._remember_headings(units, translated)
                for offset, el in enumerate(translated):
                    parent.insert(index + offset, el)
                translated[-1].tail = tail
            self.progress.advance(cached, t("tr.label", doc=posixpath.basename(doc), n=n + 1))
        for attribute in ("lang", XML_LANG):
            if root.get(attribute) is not None:
                root.set(attribute, self.target)
        book.files[doc] = _serialize(tree, book.files[doc])

    def _remember_headings(self, original: list[etree._Element], translated: list[etree._Element]) -> None:
        # The checks guarantee both have the same structure, so headings line up in order.
        def headings(units):
            return [_text([h]) for u in units for h in u.iter()
                    if _local(h.tag) in ("h1", "h2", "h3", "h4", "h5", "h6")]
        for source, target in zip(headings(original), headings(translated)):
            if source and target:
                self.headings[source] = target

    def _translate_labels(self, glossary: Glossary, labels: list[str]) -> dict[str, str]:
        """Short strings (table of contents entries, document titles) in batches."""
        path = self.work_dir / "labels.json"
        done = json.loads(path.read_text()) if path.exists() else {}
        done.update({k: v for k, v in self.headings.items() if k in labels})
        pending = [l for l in dict.fromkeys(labels) if l.strip() and l not in done]
        for start in range(0, len(pending), 150):
            batch = pending[start:start + 150]
            content = (f"## Glossary\n{glossary.text()}\n\nTranslate each string into {language_name(self.target)}. "
                       f"Return exactly {len(batch)} translations in the same order.\n\n"
                       + json.dumps(batch, ensure_ascii=False))
            try:
                result = self.engine.ask_json(self.system(TRANSLATOR_SYSTEM), content, LABELS_SCHEMA)["translations"]
            except (RefusedResponse, TruncatedResponse):
                result = []
            if len(result) == len(batch):
                done.update(zip(batch, result))
            path.write_text(json.dumps(done, ensure_ascii=False))
        return done

    def _translate_metadata(self, glossary: Glossary, book: Book) -> None:
        log(t("tr.labels"))
        ncx_tree = _xml(book.files[book.ncx]) if book.ncx else None
        ncx_texts = list(ncx_tree.getroot().iter(f"{{{NCX_NS}}}text")) if ncx_tree is not None else []
        head_titles = {}
        for doc in book.documents:
            title = _xml(book.files[doc]).getroot().find(f"{NS}head/{NS}title")
            if title is not None and title.text:
                head_titles[doc] = title.text
        labels = [re.sub(r"\s+", " ", el.text or "").strip() for el in ncx_texts] + list(head_titles.values())
        translated = self._translate_labels(glossary, labels)

        for el in ncx_texts:
            el.text = translated.get(re.sub(r"\s+", " ", el.text or "").strip(), el.text)
        if ncx_tree is not None:
            book.files[book.ncx] = _serialize(ncx_tree, book.files[book.ncx])
        for doc, text in head_titles.items():
            tree = _xml(book.files[doc])
            tree.getroot().find(f"{NS}head/{NS}title").text = translated.get(text, text)
            book.files[doc] = _serialize(tree, book.files[doc])

        opf_tree = _xml(book.files[book.opf_path])
        opf = opf_tree.getroot()
        if (title := opf.find(f".//{{{DC_NS}}}title")) is not None and glossary.title:
            title.text = glossary.title
        if (language := opf.find(f".//{{{DC_NS}}}language")) is not None:
            language.text = self.target
        book.files[book.opf_path] = _serialize(opf_tree, book.files[book.opf_path])

    def run(self) -> None:
        book = read_book(self.source)
        glossary = self.glossary(book)
        plan = [(doc, n) for doc in book.documents
                if (body := _xml(book.files[doc]).getroot().find(f"{NS}body")) is not None
                for n, _ in enumerate(chunk_units(body))]
        done = sum(self._cache(doc, n).exists() for doc, n in plan)
        self.progress = Progress(self.work_dir / "progress.json", len(plan), done)
        log(t("tr.start", name=self.source.name, target=language_name(self.target),
              docs=len(book.documents), chunks=len(plan)))
        if done:
            log(t("run.resuming", done=done, total=len(plan)))

        with ThreadPoolExecutor(max_workers=max(1, self.parallel)) as pool:
            futures = [pool.submit(self._translate_document, glossary, book, doc) for doc in book.documents]
            for future in as_completed(futures):
                future.result()

        self._translate_metadata(glossary, book)
        self.progress.finish()
        write_book(book, self.output)
        log(t("tr.done", path=self.output))
        report_usage(self.engine)


def translated_path(epub: Path, target: str) -> Path:
    return epub.with_name(f"{epub.stem}.{target}.epub")


def estimate_translation(epub: Path) -> str:
    """Tokens of exactly what is sent: the masked XHTML of every chunk (plus a glossary per call)."""
    book = read_book(epub)
    chars, calls = 0, 0
    for doc in book.documents:
        body = _xml(book.files[doc]).getroot().find(f"{NS}body")
        for units in chunk_units(body) if body is not None else []:
            masked, _ = mask(units)
            if _has_text(masked):
                chars += len(to_fragment(masked))
                calls += 1
    # Calibrated on a real run: XHTML is ~0.4 tokens per character, each call adds ~2,000
    # tokens (system prompt, glossary, Claude Code overhead), and the output, including the
    # model's reasoning, is ~0.5 tokens per character sent.
    input_tokens = chars * 0.4 + (calls + 1) * 2000
    output_tokens = chars * 0.5
    return t("tr.estimate", docs=len(book.documents), input=input_tokens / 1e6, output=output_tokens / 1e6)
