"""pdftoepub: convert PDF books (especially technical ones) to EPUB with AI agents."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import i18n
from .i18n import t


def _page_range(text: str) -> tuple[int, int]:
    a, _, b = text.partition("-")
    return int(a), int(b or a)


def main() -> None:
    i18n.set_language(i18n.detect(sys.argv[1:]))

    from .llm import DEFAULT_MODEL, ENGINES, PRICES, EngineError
    from .pipeline import Converter, Options, estimate

    ap = argparse.ArgumentParser(prog="pdftoepub", description=t("cli.description"))
    ap.add_argument("pdf", type=Path, help=t("cli.pdf"))
    ap.add_argument("-o", "--output", type=Path, help=t("cli.output"))
    ap.add_argument("--engine", default="claude-code", choices=list(ENGINES), help=t("cli.engine"))
    ap.add_argument("--model", default=DEFAULT_MODEL, help=t("cli.model", model=DEFAULT_MODEL))
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"], help=t("cli.effort"))
    ap.add_argument("--pages-per-chunk", type=int, default=6, help=t("cli.pages_per_chunk"))
    ap.add_argument("--parallel", type=int, default=3, help=t("cli.parallel"))
    ap.add_argument("--reviews", type=int, default=2, help=t("cli.reviews"))
    ap.add_argument("--pages", type=_page_range, help=t("cli.pages"))
    ap.add_argument("--no-vision", action="store_true", help=t("cli.no_vision"))
    ap.add_argument("--no-highlight", action="store_true", help=t("cli.no_highlight"))
    ap.add_argument("--no-ai", action="store_true", help=t("cli.no_ai"))
    ap.add_argument("--estimate", action="store_true", help=t("cli.estimate"))
    ap.add_argument("--lang", choices=i18n.LANGUAGES, help=t("cli.lang"))
    args = ap.parse_args()

    if not args.pdf.is_file():
        ap.error(t("cli.file_missing", path=args.pdf))
    output = args.output or args.pdf.with_suffix(".epub")
    options = Options(
        pages_per_chunk=args.pages_per_chunk,
        vision=not args.no_vision,
        parallel=args.parallel,
        reviews=args.reviews,
        syntax_highlight=not args.no_highlight,
        page_range=args.pages,
    )

    if args.estimate:
        print(estimate(args.pdf, options))
        if args.model in PRICES:
            pin, pout = PRICES[args.model]
            print(t("run.price", model=args.model, pin=pin, pout=pout))
        return

    engine = None
    if not args.no_ai:
        try:
            engine = ENGINES[args.engine](args.model, args.effort)
        except EngineError as e:
            sys.exit(str(e))

    try:
        Converter(args.pdf, output, options, engine).run()
    except KeyboardInterrupt:
        sys.exit(t("run.interrupted"))
    except EngineError as e:
        sys.exit(t("run.saved", error=e))
