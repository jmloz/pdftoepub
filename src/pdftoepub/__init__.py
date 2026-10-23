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

    from .analysis import analyze, choose_model, format_report
    from .llm import ENGINES, PRICES, EngineError
    from .pipeline import Converter, Options, estimate, read_status

    ap = argparse.ArgumentParser(prog="pdftoepub", description=t("cli.description"))
    ap.add_argument("pdf", type=Path, help=t("cli.pdf"))
    ap.add_argument("-o", "--output", type=Path, help=t("cli.output"))
    ap.add_argument("--engine", default="claude-code", choices=list(ENGINES), help=t("cli.engine"))
    ap.add_argument("--model", help=t("cli.model"))
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"], help=t("cli.effort"))
    ap.add_argument("--pages-per-chunk", type=int, default=6, help=t("cli.pages_per_chunk"))
    ap.add_argument("--parallel", type=int, default=3, help=t("cli.parallel"))
    ap.add_argument("--reviews", type=int, default=2, help=t("cli.reviews"))
    ap.add_argument("--pages", type=_page_range, help=t("cli.pages"))
    ap.add_argument("--no-vision", action="store_true", help=t("cli.no_vision"))
    ap.add_argument("--no-highlight", action="store_true", help=t("cli.no_highlight"))
    ap.add_argument("--math", default="mathml", choices=["mathml", "image"], help=t("cli.math"))
    ap.add_argument("--diagrams", default="svg", choices=["svg", "png"], help=t("cli.diagrams"))
    ap.add_argument("--no-ai", action="store_true", help=t("cli.no_ai"))
    ap.add_argument("--estimate", action="store_true", help=t("cli.estimate"))
    ap.add_argument("--status", action="store_true", help=t("cli.status"))
    ap.add_argument("--analyze", action="store_true", help=t("cli.analyze"))
    ap.add_argument("-y", "--yes", action="store_true", help=t("cli.yes"))
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
        math=args.math,
        diagrams=args.diagrams,
    )

    if args.status:
        print(read_status(output.with_suffix(".work"), args.pages_per_chunk))
        return

    if args.analyze or args.estimate:
        report = analyze(args.pdf, args.pages)
        if args.analyze:
            print(format_report(report, args.pdf.name))
        print(estimate(args.pdf, options))
        model = args.model or report.model
        if model in PRICES:
            pin, pout = PRICES[model]
            print(t("run.price", model=model, pin=pin, pout=pout))
        return

    engine = None
    if not args.no_ai:
        work_dir = output.with_suffix(".work")
        interactive = sys.stdin.isatty() and not args.yes
        model = choose_model(args.pdf, work_dir, args.model, args.pages, interactive)
        try:
            engine = ENGINES[args.engine](model, args.effort)
        except EngineError as e:
            sys.exit(str(e))

    try:
        Converter(args.pdf, output, options, engine).run()
    except KeyboardInterrupt:
        sys.exit(t("run.interrupted"))
    except EngineError as e:
        sys.exit(t("run.saved", error=e))
