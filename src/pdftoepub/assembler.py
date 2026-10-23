"""Assembles the XHTML fragments into an EPUB 3."""

from __future__ import annotations

import html as html_lib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ebooklib import epub
from lxml import etree
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .agents import CONTINUES_MARK, Profile
from .extractor import BOX_CLOSE, BOX_OPEN, CODE_MARK, MATH_CLOSE, MATH_OPEN, Page
from .verifier import MATHML_NS, XHTML_NS, parse, repair

NS = f"{{{XHTML_NS}}}"
MNS = f"{{{MATHML_NS}}}"
MEDIA_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
PYGMENTS_STYLE = "friendly"

BASE_CSS = """\
body { font-family: serif; line-height: 1.5; margin: 0 0.5em; }
h1 { font-size: 1.6em; margin: 1em 0 0.8em; page-break-before: always; line-height: 1.2; }
h2 { font-size: 1.3em; margin: 1.4em 0 0.5em; line-height: 1.25; }
h3 { font-size: 1.12em; margin: 1.2em 0 0.4em; }
h4 { font-size: 1em; margin: 1em 0 0.3em; font-style: italic; }
h2, h3, h4 { page-break-after: avoid; }
p { margin: 0.4em 0; text-align: justify; hyphens: auto; -epub-hyphens: auto; }
code, kbd, pre { font-family: "DejaVu Sans Mono", "Liberation Mono", Menlo, Consolas, monospace; }
code { font-size: 0.9em; hyphens: none; -epub-hyphens: none; }
pre {
  font-size: 0.78em; line-height: 1.35; text-align: left;
  white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word;
  background: #f5f5f3; border: 1px solid #ddd; border-left: 3px solid #999;
  padding: 0.6em 0.7em; margin: 0.8em 0; page-break-inside: avoid;
}
pre code { font-size: 1em; }
kbd { border: 1px solid #aaa; border-radius: 3px; padding: 0 0.25em; font-size: 0.85em; }
table { border-collapse: collapse; margin: 0.8em 0; font-size: 0.88em; width: 100%; }
th, td { border: 1px solid #bbb; padding: 0.25em 0.45em; text-align: left; vertical-align: top; }
th { background: #eee; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: 0.85em; font-style: italic; margin-top: 0.3em; }
aside { border: 1px solid #ccc; border-left: 4px solid #777; padding: 0.4em 0.8em; margin: 0.9em 0; background: #fafafa; }
aside.warning { border-left-color: #b33; }
aside.tip { border-left-color: #3a3; }
aside.footnote { border: none; background: none; font-size: 0.85em; padding: 0; border-top: 1px solid #ccc; }
.aside-title { font-weight: bold; margin-top: 0; }
a.noteref { vertical-align: super; font-size: 0.75em; }
dt { font-weight: bold; margin-top: 0.5em; }
dd { margin-left: 1.5em; }
.conversion-notice { color: #a00; font-style: italic; }
math[display="block"] { display: block; margin: 0.8em auto; text-align: center; }
p.formula { text-align: center; margin: 0.8em 0; }
img.formula { max-width: 100%; vertical-align: middle; }
"""


@dataclass
class FinalChapter:
    title: str
    file: str
    body: etree._Element
    sections: list[tuple[int, str, str]] = field(default_factory=list)  # (level, id, title)
    has_math: bool = False


# ---------------------------------------------------------------------------
# Merging chunks
# ---------------------------------------------------------------------------

def to_tree(fragment: str) -> etree._Element:
    try:
        return parse(fragment)
    except etree.XMLSyntaxError:
        return repair(fragment)


def _append_content(target: etree._Element, source: etree._Element, separator: str, literal: bool = False) -> None:
    """Move the text and children of `source` to the end of `target`.

    With `literal` (code) whitespace is left untouched: indentation is content.
    """
    def join(a: str, b: str) -> str:
        return a.rstrip("\n") + separator + b if literal else a.rstrip() + separator + b.lstrip()

    children = list(target)
    text = source.text or ""
    if children:
        children[-1].tail = join(children[-1].tail or "", text)
    else:
        target.text = join(target.text or "", text)
    for child in source:
        target.append(child)


def merge_chunks(fragments: list[str]) -> etree._Element:
    body = etree.Element(f"{NS}div", nsmap={None: XHTML_NS})
    for fragment in fragments:
        continues = fragment.lstrip().startswith(CONTINUES_MARK)
        elements = list(to_tree(fragment))
        if continues and elements and len(body):
            last, first = body[-1], elements[0]
            if last.tag == first.tag and first.tag in (f"{NS}p", f"{NS}li"):
                _append_content(last, first, " ")
                elements = elements[1:]
            elif last.tag == first.tag == f"{NS}pre":
                last_code, first_code = last.find(f"{NS}code"), first.find(f"{NS}code")
                if last_code is not None and first_code is not None:
                    first_code.text = (first_code.text or "").lstrip("\n")
                    _append_content(last_code, first_code, "\n", literal=True)
                    elements = elements[1:]
            elif last.tag in (f"{NS}ul", f"{NS}ol") and first.tag == f"{NS}li":
                # Loose items that continue the previous list.
                while elements and elements[0].tag == f"{NS}li":
                    last.append(elements.pop(0))
            elif last.tag == first.tag and first.tag in (f"{NS}ul", f"{NS}ol", f"{NS}table", f"{NS}dl"):
                target = last.find(f"{NS}tbody") if first.tag == f"{NS}table" else last
                source = first.find(f"{NS}tbody") if first.tag == f"{NS}table" else first
                if target is not None and source is not None:
                    for child in list(source):
                        if child.tag != f"{NS}thead":
                            target.append(child)
                    elements = elements[1:]
        for el in elements:
            body.append(el)
    return body


# ---------------------------------------------------------------------------
# Per-chapter post-processing
# ---------------------------------------------------------------------------

def _plain_text(el: etree._Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _lexer(name: str):
    aliases = {"console": "console", "shell": "bash", "sh": "bash", "terminal": "console", "zsh": "zsh",
               "text": "text", "plaintext": "text", "txt": "text", "dockerfile": "docker"}
    try:
        return get_lexer_by_name(aliases.get(name.lower(), name.lower()))
    except ClassNotFound:
        return None


def highlight_code(body: etree._Element) -> None:
    formatter = HtmlFormatter(nowrap=True)
    for code in body.iter(f"{NS}code"):
        parent = code.getparent()
        if parent is None or parent.tag != f"{NS}pre":
            continue
        m = re.search(r"language-([\w+#.-]+)", code.get("class", ""))
        lexer = _lexer(m.group(1)) if m else None
        if lexer is None or lexer.name == "Text only":
            continue
        source = "".join(code.itertext())
        colored = highlight(source, lexer, formatter).rstrip("\n")
        new = etree.fromstring(f'<code xmlns="{XHTML_NS}">{colored}</code>'.encode())
        code.text = new.text
        for child in list(code):
            code.remove(child)
        for child in new:
            code.append(child)


def normalize_mathml(body: etree._Element) -> bool:
    """Put every <math> subtree in the MathML namespace. Returns True if there is math."""
    found = False
    for math in list(body.iter(f"{NS}math", f"{MNS}math")):
        found = True
        for el in math.iter():
            if isinstance(el.tag, str) and el.tag.startswith(NS):
                el.tag = MNS + el.tag[len(NS):]
        # Rebuild the root with MathML as its default namespace, so it serializes as
        # <math xmlns="…MathML"> instead of a prefixed <ns0:math> readers don't understand.
        new = etree.Element(f"{MNS}math", attrib=dict(math.attrib), nsmap={None: MATHML_NS})
        new.text, new.tail = math.text, math.tail
        new.extend(list(math))
        math.getparent().replace(math, new)
    return found


def prepare_chapter(title: str, index: int, body: etree._Element, image_path: str, syntax_highlight: bool,
                    asset_files: dict[str, str] | None = None) -> FinalChapter:
    asset_files = asset_files or {}
    file = f"ch{index:03d}.xhtml"

    if body.find(f".//{NS}h1") is None:
        first = body[0] if len(body) else None
        if first is not None and first.tag == f"{NS}h2" and _plain_text(first).lower() == title.lower():
            first.tag = f"{NS}h1"
        else:
            h1 = etree.Element(f"{NS}h1")
            h1.text = title
            body.insert(0, h1)

    sections = []
    for n, h in enumerate(body.iter(f"{NS}h1", f"{NS}h2", f"{NS}h3")):
        h.set("id", h.get("id") or f"s{index}-{n}")
        sections.append((int(h.tag[-1]), h.get("id"), _plain_text(h)))

    for img in body.iter(f"{NS}img"):
        src = img.get("src", "")
        img.set("src", f"{image_path}/{asset_files.get(src, src + '.png')}")
        img.set("alt", img.get("alt") or "")

    has_math = normalize_mathml(body)
    if syntax_highlight:
        highlight_code(body)
    return FinalChapter(title, file, body, sections, has_math)


def basic_conversion(pages: list[Page], notice: str | None = None) -> str:
    """Conversion without AI: paragraphs and code blocks from the annotated text."""
    parts: list[str] = []
    if notice:
        parts.append(f'<p class="conversion-notice">{html_lib.escape(notice)}</p>')
    for p in pages:
        code: list[str] = []
        paragraph: list[str] = []

        def close_paragraph():
            if paragraph:
                text = re.sub(r"(\w)- (\w)", r"\1\2", " ".join(paragraph))
                parts.append(f"<p>{html_lib.escape(text)}</p>")
                paragraph.clear()

        def close_code():
            if code:
                parts.append(f'<pre><code class="language-text">{html_lib.escape(chr(10).join(code))}</code></pre>')
                code.clear()

        for line in p.text.splitlines():
            if line in (BOX_OPEN, BOX_CLOSE):
                close_paragraph()
                continue
            line = line.replace(MATH_OPEN, "").replace(MATH_CLOSE, "")
            if line.startswith(CODE_MARK):
                close_paragraph()
                code.append(line[len(CODE_MARK):])
                continue
            close_code()
            if not line.strip():
                close_paragraph()
            elif m := re.match(r"\[H[\d.]+\] (.*)", line):
                close_paragraph()
                parts.append(f"<h2>{html_lib.escape(m.group(1))}</h2>")
            else:
                paragraph.append(line.replace("**", ""))
        close_paragraph()
        close_code()
        for a in p.assets:
            if a.kind == "image":
                parts.append(f'<figure><img src="{a.id}" alt=""/></figure>')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

class _Chapter(epub.EpubHtml):
    """An XHTML document written exactly as given.

    ebooklib re-serializes chapters with pretty_print, which inserts line breaks and
    indentation inside <pre>: the first line of every code block would be shifted.
    """

    def get_content(self, default=None):
        return self.content


def _document(chapter: FinalChapter, language: str) -> bytes:
    body = etree.tostring(chapter.body, encoding="unicode")
    body = re.sub(r"^<div[^>]*>|</div>$", "", body)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops" lang="{language}" xml:lang="{language}">\n'
        f"<head>\n<title>{html_lib.escape(chapter.title)}</title>\n"
        '<link href="styles/book.css" rel="stylesheet" type="text/css"/>\n</head>\n'
        f"<body>{body}</body>\n</html>\n"
    ).encode()


def _toc(chapters: list[FinalChapter]) -> list:
    toc = []
    for ch in chapters:
        children = [epub.Link(f"{ch.file}#{id_}", title, f"{ch.file}-{id_}")
                    for level, id_, title in ch.sections if level == 2 and title]
        if children:
            toc.append((epub.Section(ch.title, href=ch.file), children))
        else:
            toc.append(epub.Link(ch.file, ch.title, ch.file))
    return toc


def build_epub(
    profile: Profile,
    chapters: list[FinalChapter],
    image_dir: Path,
    cover_path: Path | None,
    dest: Path,
    syntax_highlight: bool,
) -> None:
    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, profile.title + ''.join(profile.authors))}")
    book.set_title(profile.title)
    book.set_language(profile.language or "en")
    for author in profile.authors:
        book.add_author(author)
    if profile.description:
        book.add_metadata("DC", "description", profile.description)
    if profile.publisher:
        book.add_metadata("DC", "publisher", profile.publisher)
    if profile.subject:
        book.add_metadata("DC", "subject", profile.subject)

    if cover_path and cover_path.exists():
        book.set_cover("cover.png", cover_path.read_bytes())

    css = BASE_CSS
    if syntax_highlight:
        css += "\n" + HtmlFormatter(style=PYGMENTS_STYLE).get_style_defs("pre code")
        css = re.sub(r"pre code \{[^}]*background[^}]*\}", "", css)  # `pre` sets the background
    stylesheet = epub.EpubItem(uid="style", file_name="styles/book.css", media_type="text/css", content=css)
    book.add_item(stylesheet)

    used: set[str] = set()
    items = []
    for ch in chapters:
        for img in ch.body.iter(f"{NS}img"):
            used.add(Path(img.get("src")).name)
        item = _Chapter(title=ch.title, file_name=ch.file, lang=profile.language)
        item.content = _document(ch, profile.language or "en")
        if ch.has_math:
            item.properties.append("mathml")
        item.add_item(stylesheet)
        book.add_item(item)
        items.append(item)

    for name in sorted(used):
        path = image_dir / name
        if path.exists():
            media_type = MEDIA_TYPES.get(path.suffix.lower(), "image/png")
            book.add_item(epub.EpubImage(uid=Path(name).stem, file_name=f"images/{name}",
                                         media_type=media_type, content=path.read_bytes()))

    book.toc = _toc(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = (["cover"] if cover_path else []) + ["nav"] + items
    epub.write_epub(str(dest), book, {"epub3_pages": False})
