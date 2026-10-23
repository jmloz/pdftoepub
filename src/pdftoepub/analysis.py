"""Pre-conversion analysis (no AI): what kind of PDF is this, and which model fits it?

A sample of pages is inspected for the things that actually make a book hard for the
agents: scanned pages, math, multi-column layouts, ruled tables and garbled text.
Clean, single-column books (most programming books) convert just as well with Sonnet
at about half the usage; the hard cases are worth Opus.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .extractor import MATH_OPEN, _page_lines

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
SAMPLE_PAGES = 80

# A feature is "significant" when it appears on at least this fraction of the sampled pages.
SCANNED_THRESHOLD = 0.10
MATH_THRESHOLD = 0.15
COLUMNS_THRESHOLD = 0.20
TABLES_THRESHOLD = 0.25
GARBLED_THRESHOLD = 0.05


@dataclass
class Report:
    pages: int
    sampled: int
    scanned: float = 0.0  # fraction of sampled pages without extractable text
    math: float = 0.0  # … with formulas (math fonts)
    columns: float = 0.0  # … with a multi-column layout
    tables: float = 0.0  # … with ruled tables
    frames: float = 0.0  # … with framed boxes (sidebars, notes)
    code: float = 0.0  # … with code (monospaced lines)
    images: float = 0.0  # … with images
    garbled: float = 0.0  # … whose extracted text is mostly unreadable
    model: str = SONNET
    reasons: list[tuple[str, dict]] = field(default_factory=list)  # (message key, values)
    tips: list[tuple[str, dict]] = field(default_factory=list)


def _has_table(page: pymupdf.Page) -> bool:
    """Cheap ruled-table detection (PyMuPDF's find_tables is ~200 ms per page).

    A table has at least 3 stroked horizontal rules of the same width and a column
    separator *inside* them that crosses at least two rules. Code boxes and sidebars only
    have lines at their edges, and shaded backgrounds (fill only) are not rules.
    """
    horizontal: list[tuple[float, float, float]] = []  # (x0, x1, y)
    vertical: list[tuple[float, float, float]] = []  # (x, y0, y1)

    def add_line(p1, p2):
        if abs(p1.y - p2.y) < 1 and abs(p1.x - p2.x) > 30:
            horizontal.append((min(p1.x, p2.x), max(p1.x, p2.x), p1.y))
        elif abs(p1.x - p2.x) < 1 and abs(p1.y - p2.y) > 8:
            vertical.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))

    for path in page.get_drawings():
        if "s" not in (path.get("type") or ""):
            continue  # fill only: a shaded background, not a rule
        for item in path["items"]:
            if item[0] == "l":
                add_line(item[1], item[2])
            elif item[0] == "re":
                r = item[1]
                for a, b in ((r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)):
                    add_line(a, b)

    rows: dict[tuple[int, int], list[float]] = {}
    for x0, x1, y in horizontal:
        rows.setdefault((round(x0 / 4), round(x1 / 4)), []).append(y)
    for (gx0, gx1), ys in rows.items():
        ys = sorted(set(round(y) for y in ys))
        if len(ys) < 3:
            continue
        x0, x1 = gx0 * 4, gx1 * 4
        for x, y0, y1 in vertical:
            crossed = sum(y0 - 2 <= y <= y1 + 2 for y in ys)
            if x0 + 8 < x < x1 - 8 and crossed >= 2:
                return True
    return False


def _is_garbled(text: str) -> bool:
    """Text from fonts without a Unicode mapping: private-use or replacement characters."""
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 50:
        return False
    bad = sum(1 for c in chars if c == "�" or 0xE000 <= ord(c) <= 0xF8FF)
    return bad / len(chars) > 0.1


def analyze(pdf: Path, page_range: tuple[int, int] | None = None) -> Report:
    doc = pymupdf.open(pdf)
    first, last = page_range or (1, doc.page_count)
    last = min(last, doc.page_count)
    count = last - first + 1
    if count <= SAMPLE_PAGES:
        indices = list(range(first - 1, last))
    else:
        indices = sorted({first - 1 + round(i * (count - 1) / (SAMPLE_PAGES - 1)) for i in range(SAMPLE_PAGES)})

    totals = dict(scanned=0, math=0, columns=0, tables=0, frames=0, code=0, images=0, garbled=0)
    for i in indices:
        page = doc[i]
        lines, columns, frames = _page_lines(page)
        text = "".join(l["text"] for l in lines)
        has_images = bool(page.get_images())
        if len(text.strip()) < 20:
            totals["scanned"] += has_images  # an image page with no text: a scan
            continue
        # One display formula, or several bits of inline math (a lone symbol is not enough).
        totals["math"] += any(l["math"] for l in lines) or sum(MATH_OPEN in l["text"] for l in lines) >= 2
        totals["columns"] += columns > 1
        totals["tables"] += _has_table(page)
        totals["frames"] += bool(frames)
        totals["code"] += sum(l["mono"] for l in lines) >= 3
        totals["images"] += has_images
        totals["garbled"] += _is_garbled(text)

    n = max(len(indices), 1)
    report = Report(pages=count, sampled=len(indices), **{k: v / n for k, v in totals.items()})
    _recommend(report)
    return report


def _pct(x: float) -> int:
    return round(x * 100)


def _recommend(r: Report) -> None:
    checks = [
        (r.scanned, SCANNED_THRESHOLD, "an.reason_scanned"),
        (r.math, MATH_THRESHOLD, "an.reason_math"),
        (r.columns, COLUMNS_THRESHOLD, "an.reason_columns"),
        (r.tables, TABLES_THRESHOLD, "an.reason_tables"),
        (r.garbled, GARBLED_THRESHOLD, "an.reason_garbled"),
    ]
    r.reasons = [(key, {"pct": _pct(value)}) for value, threshold, key in checks if value >= threshold]
    if r.reasons:
        r.model = OPUS
    else:
        r.model = SONNET
        r.reasons = [("an.reason_simple", {"code": _pct(r.code)})]

    # Usage tips that don't depend on the model.
    needs_vision = r.scanned or r.tables >= 0.05 or r.math or r.images >= 0.1 or r.columns or r.frames >= 0.1
    if not needs_vision:
        r.tips.append(("an.tip_no_vision", {}))
    if r.math >= MATH_THRESHOLD:
        r.tips.append(("an.tip_math_kindle", {}))
    if r.scanned >= SCANNED_THRESHOLD:
        r.tips.append(("an.tip_scanned", {}))


def format_report(r: Report, pdf_name: str) -> str:
    from .i18n import t

    features = [
        ("an.f_scanned", r.scanned), ("an.f_math", r.math), ("an.f_columns", r.columns),
        ("an.f_tables", r.tables), ("an.f_frames", r.frames), ("an.f_code", r.code),
        ("an.f_images", r.images), ("an.f_garbled", r.garbled),
    ]
    lines = [t("an.header", name=pdf_name, sampled=r.sampled, pages=r.pages)]
    lines += [f"  {t(key):<34} {_pct(value):>3}%" for key, value in features]
    label = t("an.opus") if r.model == OPUS else t("an.sonnet")
    lines.append(t("an.recommend", model=r.model, label=label))
    lines += [f"   · {t(key, **values)}" for key, values in r.reasons]
    lines += [t(key, **values) for key, values in r.tips]
    return "\n".join(lines)


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def saved_model(work_dir: Path) -> str | None:
    path = work_dir / "settings.json"
    if path.exists():
        return json.loads(path.read_text()).get("model")
    return None


def save_model(work_dir: Path, model: str) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "settings.json").write_text(json.dumps({"model": model}))


def choose_model(pdf: Path, work_dir: Path, explicit: str | None, page_range: tuple[int, int] | None,
                 interactive: bool, ask=input, out=_stderr) -> str:
    """Pick the model for a conversion.

    1. `--model` always wins (with a note if the analysis recommends another one).
    2. A resumed conversion keeps the model it started with, so a book isn't silently mixed.
    3. Otherwise the analysis recommends one; in a terminal the user confirms it.
    """
    from .i18n import t

    if explicit:
        recommended = analyze(pdf, page_range).model
        out(t("an.model_chosen", model=explicit))
        if recommended != explicit:
            out(t("an.model_differs", model=recommended))
        return explicit

    if saved := saved_model(work_dir):
        out(t("an.model_resumed", model=saved))
        return saved

    report = analyze(pdf, page_range)
    out(format_report(report, pdf.name))
    model = report.model
    if interactive:
        other = OPUS if model == SONNET else SONNET
        if ask(t("an.ask", model=model, other=other)).strip().lower() in ("n", "no"):
            model = other
    out(t("an.model_recommended" if model == report.model else "an.model_chosen", model=model))
    return model
