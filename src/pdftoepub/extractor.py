"""Deterministic PDF extraction (no AI).

Turns each page into annotated text that keeps the typographic hints the agents
need to rebuild the book faithfully:

- Text in reading order, even on multi-column pages.
- Lines in a monospaced font     -> marked as code (`¦ `).
- Lines larger than body text    -> heading candidates (`[H<size>]`).
- Text in a math font            -> wrapped in `⟦…⟧`.
- Text inside a drawn frame      -> wrapped in `[BOX]` … `[/BOX]` (sidebars, notes…).
- Raster images, vector diagrams (SVG or PNG) and, optionally, display formulas
  -> exported with an ID.
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
MATH_OPEN, MATH_CLOSE = "⟦", "⟧"
BOX_OPEN, BOX_CLOSE = "[BOX]", "[/BOX]"

MIN_IMAGE_SIDE = 48  # px; skips icons, bullets and ornaments
MIN_DIAGRAM_AREA = 0.04  # fraction of the page area
MIN_BOX_AREA = 0.02  # frames smaller than this (e.g. table cells) are ignored
MAX_BOX_AREA = 0.8  # frames larger than this are page borders
MAX_SVG_BYTES = 600_000  # heavier SVGs fall back to PNG
WIDE_BLOCK = 0.55  # blocks wider than this fraction of the text area span all columns
MARGIN_BAND = 0.08  # top/bottom band where running headers and footers live

MONO_FONTS = re.compile(
    r"mono|courier|consol|menlo|inconsolata|source\s?code|code|fira\s?mono|"
    r"dejavu\s?sans\s?mono|liberation\s?mono|lucida\s?console|cmtt|sfmono|"
    r"roboto\s?mono|ubuntu\s?mono|jetbrains|hack|andale|letter\s?gothic|ocr",
    re.IGNORECASE,
)
MATH_FONTS = re.compile(
    r"math|cmmi|cmsy|cmex|cmbsy|cmmib|msam|msbm|eufm|eusm|rsfs|stix|xits|symbol|"
    r"mt\s?extra|euclid|txmi|txsy|txex|pxmi|pxsy|lmmath|mathematical\s?pi",
    re.IGNORECASE,
)
NOT_MATH = set("•∙·◦–—-*")  # bullets and dashes often set in math fonts


@dataclass
class Asset:
    id: str
    file: str  # file name inside the images folder
    page: int
    kind: str  # "image" | "diagram" | "formula"
    width: int
    height: int


@dataclass
class Page:
    number: int  # 1-based
    text: str  # annotated text
    assets: list[Asset] = field(default_factory=list)
    no_text: bool = False  # probably scanned
    columns: int = 1

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

    def asset_files(self) -> dict[str, str]:
        return {a.id: a.file for p in self.pages for a in p.assets}


@dataclass
class ExtractOptions:
    diagrams: str = "svg"  # "svg" | "png"
    math: str = "mathml"  # "mathml" | "image"


# ---------------------------------------------------------------------------
# Lines and spans
# ---------------------------------------------------------------------------

def _is_mono(span: dict) -> bool:
    return bool(span["flags"] & pymupdf.TEXT_FONT_MONOSPACED) or bool(MONO_FONTS.search(span["font"]))


def _is_math(span: dict) -> bool:
    return bool(MATH_FONTS.search(span["font"])) and span["text"].strip() not in NOT_MATH


def _normalize_margin(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _line(line: dict, block_id: int, page_height: float) -> dict | None:
    spans = [s for s in line["spans"] if s["text"].strip()]
    if not spans:
        return None
    chars = sum(len(s["text"]) for s in spans)
    mono = sum(len(s["text"]) for s in spans if _is_mono(s)) / chars > 0.6
    math_chars = sum(len(s["text"]) for s in spans if _is_math(s))

    # Text with math runs wrapped in ⟦…⟧ (not inside code).
    parts, in_math = [], False
    for s in line["spans"]:
        is_math = not mono and s["text"].strip() and _is_math(s)
        if is_math and not in_math:
            parts.append(MATH_OPEN)
        elif not is_math and in_math and s["text"].strip():
            parts.append(MATH_CLOSE)
        if s["text"].strip():
            in_math = bool(is_math)
        parts.append(s["text"])
    if in_math:
        parts.append(MATH_CLOSE)

    x0, y0, x1, y1 = line["bbox"]
    return {
        "text": "".join(parts).rstrip(),
        "bbox": pymupdf.Rect(line["bbox"]),
        "x0": x0,
        "mono": mono,
        "math": not mono and math_chars / chars > 0.6,  # a display formula line
        "bold": all(s["flags"] & pymupdf.TEXT_FONT_BOLD for s in spans),
        "size": round(max(s["size"] for s in spans), 1),
        "margin": y1 < page_height * MARGIN_BAND or y0 > page_height * (1 - MARGIN_BAND),
        "block": block_id,
        "box": None,
        "column": 0,
    }


# ---------------------------------------------------------------------------
# Reading order: frames and columns
# ---------------------------------------------------------------------------

def _frames(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Drawn rectangles that may frame a sidebar, note, listing or table."""
    area = page.rect.width * page.rect.height
    frames = []
    for path in page.get_drawings():
        r = path["rect"]
        if MIN_BOX_AREA * area <= r.width * r.height <= MAX_BOX_AREA * area and r.height > 20:
            frames.append(r)
    # Drop near-duplicate rectangles (fill + stroke of the same frame).
    unique: list[pymupdf.Rect] = []
    for r in sorted(frames, key=lambda r: r.width * r.height):
        if not any(abs(r.x0 - u.x0) < 3 and abs(r.y0 - u.y0) < 3 and abs(r.x1 - u.x1) < 3 and abs(r.y1 - u.y1) < 3
                   for u in unique):
            unique.append(r)
    return unique


def _side_by_side(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return overlap > 0.5 * min(a.height, b.height)


def _order_units(units: list[dict]) -> tuple[list[dict], int]:
    """Sort layout units (blocks or framed groups) into reading order.

    Units wider than WIDE_BLOCK of the text area (titles, full-width figures,
    single-column paragraphs) split the page into horizontal bands. Inside a band of
    narrow units, units are grouped into columns by horizontal overlap; if the columns
    really sit side by side they are read one after another, otherwise top to bottom.
    """
    if not units:
        return [], 1
    left = min(u["rect"].x0 for u in units)
    right = max(u["rect"].x1 for u in units)
    width = max(right - left, 1)

    bands: list[list[dict]] = []
    current: list[dict] = []
    for u in sorted(units, key=lambda u: (u["rect"].y0, u["rect"].x0)):
        if u["rect"].width > WIDE_BLOCK * width:
            if current:
                bands.append(current)
                current = []
            bands.append([u])
        else:
            current.append(u)
    if current:
        bands.append(current)

    ordered, max_columns = [], 1
    for band in bands:
        columns: list[dict] = []  # {"rect": Rect, "units": [...]}
        for u in sorted(band, key=lambda u: u["rect"].x0):
            for col in columns:
                if u["rect"].x0 < col["rect"].x1 and u["rect"].x1 > col["rect"].x0:
                    col["units"].append(u)
                    col["rect"] |= u["rect"]
                    break
            else:
                columns.append({"rect": pymupdf.Rect(u["rect"]), "units": [u]})
        columns.sort(key=lambda c: c["rect"].x0)
        multi = len(columns) > 1 and all(_side_by_side(a["rect"], b["rect"]) for a, b in zip(columns, columns[1:]))
        if multi:
            max_columns = max(max_columns, len(columns))
            for i, col in enumerate(columns):
                for u in sorted(col["units"], key=lambda u: u["rect"].y0):
                    u["column"] = i
                    ordered.append(u)
        else:
            ordered.extend(sorted(band, key=lambda u: (u["rect"].y0, u["rect"].x0)))
    return ordered, max_columns


def _page_lines(page: pymupdf.Page) -> tuple[list[dict], int, list[pymupdf.Rect]]:
    """The page's text lines in reading order, the column count and the text frames used."""
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    blocks = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        lines = [l for l in (_line(line, block["number"], page.rect.height) for line in block["lines"]) if l]
        if lines:
            blocks.append({"rect": pymupdf.Rect(block["bbox"]), "lines": lines})

    # Blocks inside the same frame are kept together as one unit.
    # Frames are sorted smallest first, so each block goes to its innermost frame.
    # A frame holding most of the page's text is page decoration, not a sidebar.
    frames = [f for f in _frames(page)
              if sum((f + (-2, -2, 2, 2)).contains(b["rect"]) for b in blocks) <= 0.7 * len(blocks)]
    units: list[dict] = []
    framed: dict[int, dict] = {}
    for b in blocks:
        frame = next((i for i, f in enumerate(frames) if (f + (-2, -2, 2, 2)).contains(b["rect"])), None)
        if frame is None:
            units.append({"rect": b["rect"], "blocks": [b], "box": None})
        else:
            unit = framed.setdefault(frame, {"rect": pymupdf.Rect(frames[frame]), "blocks": [], "box": frame})
            unit["blocks"].append(b)
    units.extend(framed.values())

    ordered, columns = _order_units(units)
    used_frames = [frames[i] for i in framed]
    lines = []
    for u in ordered:
        for b in sorted(u["blocks"], key=lambda b: (b["rect"].y0, b["rect"].x0)):
            for l in b["lines"]:
                l["box"] = u["box"]
                l["column"] = u.get("column", 0)
                lines.append(l)
    return lines, columns, used_frames


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def _save_region(doc: pymupdf.Document, page: pymupdf.Page, rect: pymupdf.Rect, dest_dir: Path,
                 asset_id: str, as_svg: bool) -> tuple[str, int, int]:
    """Export a page region as SVG (vector) or PNG. Returns (file, width, height)."""
    if as_svg:
        tmp = pymupdf.open()
        target = tmp.new_page(width=rect.width, height=rect.height)
        target.show_pdf_page(target.rect, doc, page.number, clip=rect)
        svg = target.get_svg_image(text_as_path=True)
        if len(svg.encode()) <= MAX_SVG_BYTES:
            file = f"{asset_id}.svg"
            (dest_dir / file).write_text(svg)
            return file, round(rect.width), round(rect.height)
    pix = page.get_pixmap(clip=rect, dpi=200)
    file = f"{asset_id}.png"
    pix.save(dest_dir / file)
    return file, pix.width, pix.height


def _merge_nearby(rects: list[pymupdf.Rect], gap: float = 20) -> list[pymupdf.Rect]:
    """Merge drawing clusters closer than `gap`: a diagram made of separate strokes is one figure."""
    merged = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                if (merged[i] + (-gap, -gap, gap, gap)).intersects(merged[j]):
                    merged[i] |= merged.pop(j)
                    changed = True
                    break
            if changed:
                break
    return merged


def _same_rect(a: pymupdf.Rect, b: pymupdf.Rect, tolerance: float = 6) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _export_assets(doc: pymupdf.Document, page: pymupdf.Page, lines: list[dict], frames: list[pymupdf.Rect],
                   image_dir: Path, options: ExtractOptions) -> list[Asset]:
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

    # Vector diagrams are exported so they are not lost. The agent decides whether
    # each one is a real diagram or just table borders / code boxes.
    page_area = page.rect.width * page.rect.height
    try:
        clusters = _merge_nearby(page.cluster_drawings())
    except Exception:
        clusters = []
    for i, rect in enumerate(clusters):
        if rect.width * rect.height < page_area * MIN_DIAGRAM_AREA:
            continue
        if any(_same_rect(rect, f) for f in frames):
            continue  # the border of a sidebar/note, already marked as [BOX]
        asset_id = f"vec_p{n}_{i + 1}"
        clip = (rect + (-4, -4, 4, 4)) & page.rect
        file, w, h = _save_region(doc, page, clip, image_dir, asset_id, options.diagrams == "svg")
        assets.append(Asset(asset_id, file, n, "diagram", w, h))

    # Display formulas as images (only in --math image mode).
    if options.math == "image":
        regions: list[pymupdf.Rect] = []
        for l in lines:
            if not l["math"]:
                continue
            if regions and l["bbox"].y0 - regions[-1].y1 < l["size"] * 0.8:
                regions[-1] |= l["bbox"]
            else:
                regions.append(pymupdf.Rect(l["bbox"]))
        for i, rect in enumerate(regions):
            asset_id = f"math_p{n}_{i + 1}"
            clip = (rect + (-3, -3, 3, 3)) & page.rect
            file, w, h = _save_region(doc, page, clip, image_dir, asset_id, options.diagrams == "svg")
            assets.append(Asset(asset_id, file, n, "formula", w, h))
    return assets


# ---------------------------------------------------------------------------
# Annotated text
# ---------------------------------------------------------------------------

def _repeated_margins(all_lines: list[list[dict]]) -> set[str]:
    """Text found in the margin of many pages: running headers, footers, page numbers."""
    counts = Counter()
    for lines in all_lines:
        counts.update({_normalize_margin(l["text"]) for l in lines if l["margin"]})
    threshold = max(3, len(all_lines) * 0.15)
    repeated = {text for text, n in counts.items() if n >= threshold}
    repeated.add("#")  # bare page numbers
    return repeated


def _annotate(lines: list[dict], repeated: set[str], body_size: float) -> str:
    out: list[str] = []
    previous_block = None
    current_box = None
    # Code indentation is measured from the leftmost code line of the same column.
    min_code_x: dict[int, float] = {}
    for l in lines:
        if l["mono"]:
            min_code_x[l["column"]] = min(min_code_x.get(l["column"], l["x0"]), l["x0"])

    for l in lines:
        if l["margin"] and _normalize_margin(l["text"]) in repeated:
            continue
        if l["box"] != current_box:
            if current_box is not None:
                out.append(BOX_CLOSE)
            if l["box"] is not None:
                out.append(BOX_OPEN)
            current_box = l["box"]
            previous_block = None
        if previous_block is not None and l["block"] != previous_block and not l["mono"]:
            out.append("")  # paragraph break
        previous_block = l["block"]
        if l["mono"]:
            # A monospaced character is ≈ 0.6 × the font size wide.
            indent = max(0, round((l["x0"] - min_code_x[l["column"]]) / (l["size"] * 0.6)))
            out.append(CODE_MARK + " " * indent + l["text"])
        elif l["size"] >= body_size * 1.15 and not l["math"]:
            out.append(f"[H{l['size']:g}] {l['text'].strip()}")
        elif l["bold"] and not l["math"]:
            out.append(f"**{l['text'].strip()}**")
        else:
            out.append(l["text"].strip())
    if current_box is not None:
        out.append(BOX_CLOSE)
    return "\n".join(out).strip()


def extract(pdf_path: Path, image_dir: Path, page_range: tuple[int, int] | None = None,
            options: ExtractOptions | None = None) -> Document:
    options = options or ExtractOptions()
    image_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        raise ValueError(t("err.password"))
    first, last = page_range or (1, doc.page_count)
    last = min(last, doc.page_count)
    indices = range(first - 1, last)

    layouts = [_page_lines(doc[i]) for i in indices]
    all_lines = [lines for lines, _, _ in layouts]
    sizes = Counter()
    for lines in all_lines:
        for l in lines:
            if not l["mono"]:
                sizes[l["size"]] += len(l["text"])
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0
    repeated = _repeated_margins(all_lines)

    pages: list[Page] = []
    candidates: list[tuple[int, float, str]] = []
    for i, (lines, columns, frames) in zip(indices, layouts):
        text = _annotate(lines, repeated, body_size)
        assets = _export_assets(doc, doc[i], lines, frames, image_dir, options)
        pages.append(Page(i + 1, text, assets, no_text=len(text) < 20, columns=columns))
        for l in lines:
            if l["size"] >= body_size * 1.3 and not l["mono"] and not l["math"] and len(l["text"].strip()) > 1:
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
