"""Deterministic PDF extraction (no AI).

Turns each page into annotated text that keeps the typographic hints the agents
need to rebuild the book faithfully:

- Lines in a monospaced font     -> marked as code (`¦ `).
- Lines larger than body text    -> heading candidates (`[H<size>]`).
- Raster images and vector diagrams -> exported as PNG with an ID.
- Repeated running headers / footers -> removed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .i18n import t

CODE_MARK = "¦ "
MIN_IMAGE_SIDE = 48  # px; skips icons, bullets and ornaments
MIN_DIAGRAM_AREA = 0.04  # fraction of the page area
MARGIN_BAND = 0.08  # top/bottom band where running headers and footers live

MONO_FONTS = re.compile(
    r"mono|courier|consol|menlo|inconsolata|source\s?code|code|fira\s?mono|"
    r"dejavu\s?sans\s?mono|liberation\s?mono|lucida\s?console|cmtt|sfmono|"
    r"roboto\s?mono|ubuntu\s?mono|jetbrains|hack|andale|letter\s?gothic|ocr",
    re.IGNORECASE,
)


@dataclass
class Asset:
    id: str
    file: str  # file name inside the images folder
    page: int
    kind: str  # "image" | "diagram"
    width: int
    height: int


@dataclass
class Page:
    number: int  # 1-based
    text: str  # annotated text
    assets: list[Asset] = field(default_factory=list)
    no_text: bool = False  # probably scanned

    @property
    def code_lines(self) -> list[str]:
        return [l[len(CODE_MARK):] for l in self.text.splitlines() if l.startswith(CODE_MARK)]


@dataclass
class Document:
    path: Path
    page_count: int
    metadata: dict
    outline: list[tuple[int, str, int]]  # (level, title, page)
    body_size: float
    pages: list[Page]
    heading_candidates: list[tuple[int, float, str]]  # (page, size, text)


def _is_mono(span: dict) -> bool:
    return bool(span["flags"] & pymupdf.TEXT_FONT_MONOSPACED) or bool(MONO_FONTS.search(span["font"]))


def _normalize_margin(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _lines(page: pymupdf.Page) -> list[dict]:
    """Return the page's text lines with their typographic information."""
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    height = page.rect.height
    lines = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).rstrip()
            chars = sum(len(s["text"]) for s in spans)
            mono = sum(len(s["text"]) for s in spans if _is_mono(s)) / chars > 0.6
            bold = all(s["flags"] & pymupdf.TEXT_FONT_BOLD for s in spans)
            size = max(s["size"] for s in spans)
            y0, y1 = line["bbox"][1], line["bbox"][3]
            lines.append(
                {
                    "text": text,
                    "x0": line["bbox"][0],
                    "y0": y0,
                    "mono": mono,
                    "bold": bold,
                    "size": round(size, 1),
                    "margin": y1 < height * MARGIN_BAND or y0 > height * (1 - MARGIN_BAND),
                    "block": block["number"],
                }
            )
    return lines


def _repeated_margins(all_lines: list[list[dict]]) -> set[str]:
    """Text found in the margin of many pages: running headers, footers, page numbers."""
    counts = Counter()
    for lines in all_lines:
        counts.update({_normalize_margin(l["text"]) for l in lines if l["margin"]})
    threshold = max(3, len(all_lines) * 0.15)
    repeated = {text for text, n in counts.items() if n >= threshold}
    repeated.add("#")  # bare page numbers
    return repeated


def _export_assets(doc: pymupdf.Document, page: pymupdf.Page, image_dir: Path) -> list[Asset]:
    assets: list[Asset] = []
    n = page.number + 1
    seen: set[int] = set()

    for i, info in enumerate(page.get_images(full=True)):
        xref = info[0]
        if xref in seen:
            continue
        seen.add(xref)
        try:
            pix = pymupdf.Pixmap(doc, xref)
            if pix.width < MIN_IMAGE_SIDE or pix.height < MIN_IMAGE_SIDE:
                continue
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            asset_id = f"img_p{n}_{i + 1}"
            file = f"{asset_id}.png"
            pix.save(image_dir / file)
            assets.append(Asset(asset_id, file, n, "image", pix.width, pix.height))
        except Exception:
            continue

    # Vector diagrams are rasterized so they are not lost. The agent decides whether
    # each one is a real diagram or just table borders / code boxes.
    page_area = page.rect.width * page.rect.height
    try:
        clusters = page.cluster_drawings()
    except Exception:
        clusters = []
    for i, rect in enumerate(clusters):
        if rect.width * rect.height < page_area * MIN_DIAGRAM_AREA:
            continue
        asset_id = f"vec_p{n}_{i + 1}"
        file = f"{asset_id}.png"
        pix = page.get_pixmap(clip=rect + (-4, -4, 4, 4), dpi=200)
        pix.save(image_dir / file)
        assets.append(Asset(asset_id, file, n, "diagram", pix.width, pix.height))
    return assets


def _annotate(lines: list[dict], repeated: set[str], body_size: float) -> str:
    out: list[str] = []
    previous_block = None
    min_code_x = min((l["x0"] for l in lines if l["mono"]), default=0)
    for l in lines:
        if l["margin"] and _normalize_margin(l["text"]) in repeated:
            continue
        if previous_block is not None and l["block"] != previous_block and not l["mono"]:
            out.append("")  # paragraph break
        previous_block = l["block"]
        if l["mono"]:
            # Rebuild code indentation from the horizontal position
            # (a monospaced character is ≈ 0.6 × the font size wide).
            indent = max(0, round((l["x0"] - min_code_x) / (l["size"] * 0.6)))
            out.append(CODE_MARK + " " * indent + l["text"])
        elif l["size"] >= body_size * 1.15:
            out.append(f"[H{l['size']:g}] {l['text'].strip()}")
        elif l["bold"]:
            out.append(f"**{l['text'].strip()}**")
        else:
            out.append(l["text"].strip())
    return "\n".join(out).strip()


def extract(pdf_path: Path, image_dir: Path, page_range: tuple[int, int] | None = None) -> Document:
    image_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        raise ValueError(t("err.password"))
    first, last = page_range or (1, doc.page_count)
    last = min(last, doc.page_count)
    indices = range(first - 1, last)

    all_lines = [_lines(doc[i]) for i in indices]
    sizes = Counter()
    for lines in all_lines:
        for l in lines:
            if not l["mono"]:
                sizes[l["size"]] += len(l["text"])
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0
    repeated = _repeated_margins(all_lines)

    pages: list[Page] = []
    candidates: list[tuple[int, float, str]] = []
    for i, lines in zip(indices, all_lines):
        text = _annotate(lines, repeated, body_size)
        assets = _export_assets(doc, doc[i], image_dir)
        pages.append(Page(i + 1, text, assets, no_text=len(text) < 20))
        for l in lines:
            if l["size"] >= body_size * 1.3 and not l["mono"] and len(l["text"].strip()) > 1:
                candidates.append((i + 1, l["size"], l["text"].strip()[:120]))

    return Document(
        path=pdf_path,
        page_count=doc.page_count,
        metadata={k: v for k, v in (doc.metadata or {}).items() if v},
        outline=[(level, title, page) for level, title, page in doc.get_toc(simple=True)],
        body_size=body_size,
        pages=pages,
        heading_candidates=candidates,
    )


def sub_pdf(pdf_path: Path, first: int, last: int) -> bytes:
    """Bytes of a PDF containing only pages [first, last] (1-based)."""
    source = pymupdf.open(pdf_path)
    new = pymupdf.open()
    new.insert_pdf(source, from_page=first - 1, to_page=last - 1)
    return new.tobytes(garbage=3, deflate=True)


def render_cover(pdf_path: Path, dest: Path) -> Path:
    page = pymupdf.open(pdf_path)[0]
    page.get_pixmap(dpi=150).save(dest)
    return dest
