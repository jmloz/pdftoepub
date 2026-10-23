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

    from .analysis import SONNET, analyze, choose_model, format_report, save_model, saved_model
    from .llm import ENGINES, PRICES, EngineError
    from .pipeline import Converter, Options, estimate, read_status
    from .translator import Translator, estimate_translation, translated_path

    ap = argparse.ArgumentParser(prog="pdftoepub", description=t("cli.description"))
    ap.add_argument("pdf", metavar="INPUT", type=Path, help=t("cli.pdf"))
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
    ap.add_argument("--translate", metavar="LANG", help=t("cli.translate"))
    ap.add_argument("-y", "--yes", action="store_true", help=t("cli.yes"))
    ap.add_argument("--lang", choices=i18n.LANGUAGES, help=t("cli.lang"))
    args = ap.parse_args()

    if not args.pdf.is_file():
        ap.error(t("cli.file_missing", path=args.pdf))
    is_epub = args.pdf.suffix.lower() == ".epub"
    if is_epub and not args.translate:
        ap.error(t("tr.need_target"))
    # For an EPUB, -o is the translated book; for a PDF, it is the converted EPUB
    # (its translation goes next to it as <name>.<lang>.epub).
    output = args.output or (translated_path(args.pdf, args.translate) if is_epub else args.pdf.with_suffix(".epub"))
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

    if is_epub and (args.analyze or args.estimate):
        print(estimate_translation(args.pdf))
        model = args.model or SONNET
        if model in PRICES:
            pin, pout = PRICES[model]
            print(t("run.price", model=model, pin=pin, pout=pout))
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

    def translate(source: Path, dest: Path) -> None:
        if engine is None:
            sys.exit(t("tr.need_ai"))
        save_model(dest.with_suffix(".work"), engine.model)
        Translator(source, dest, args.translate, engine, args.parallel, args.reviews).run()

    engine = None
    if not args.no_ai:
        work_dir = output.with_suffix(".work")
        if is_epub:
            # Translating prose is well within Sonnet's reach: no analysis needed.
            model = args.model or saved_model(work_dir) or SONNET
            print(t("tr.model", model=model), file=sys.stderr)
        else:
            interactive = sys.stdin.isatty() and not args.yes
            model = choose_model(args.pdf, work_dir, args.model, args.pages, interactive)
        try:
            engine = ENGINES[args.engine](model, args.effort)
        except EngineError as e:
            sys.exit(str(e))

    try:
        if is_epub:
            translate(args.pdf, output)
        else:
            Converter(args.pdf, output, options, engine).run()
            if args.translate:
                translate(output, translated_path(output, args.translate))
    except KeyboardInterrupt:
        sys.exit(t("run.interrupted"))
    except EngineError as e:
        sys.exit(t("run.saved", error=e))
