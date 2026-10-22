"""Pipeline: coordinates extraction, agents, verification and assembly.

All intermediate work is saved in a work folder, so an interrupted conversion
resumes where it stopped when run again. It also lets you hand-edit
`profile.json` (e.g. to fix chapter boundaries) and run again.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from . import agents, assembler, extractor, verifier
from .agents import Chapter, Profile
from .extractor import Document, ExtractOptions, Page
from .i18n import t
from .llm import ClaudeCode, Engine, RefusedResponse, TruncatedResponse

MAX_CHUNK_PDF = 24 * 1024 * 1024  # bytes; the API accepts 32 MB per request
MAX_CHUNK_TEXT = 45_000  # characters of extracted text per chunk


@dataclass
class Options:
    pages_per_chunk: int = 6
    vision: bool = True
    parallel: int = 3
    reviews: int = 2
    syntax_highlight: bool = True
    page_range: tuple[int, int] | None = None
    math: str = "mathml"  # "mathml" | "image"
    diagrams: str = "svg"  # "svg" | "png"


_log_lock = threading.Lock()


def log(message: str) -> None:
    with _log_lock:
        print(message, file=sys.stderr, flush=True)


def _chunks(pages: list[Page], size: int) -> list[list[Page]]:
    chunks, current, chars = [], [], 0
    for p in pages:
        if current and (len(current) >= size or chars + len(p.text) > MAX_CHUNK_TEXT):
            chunks.append(current)
            current, chars = [], 0
        current.append(p)
        chars += len(p.text)
    if current:
        chunks.append(current)
    return chunks


class Converter:
    def __init__(self, pdf: Path, output: Path, options: Options, engine: Engine | None):
        self.pdf = pdf
        self.output = output
        self.opt = options
        self.engine = engine
        self.work_dir = output.with_suffix(".work")
        self.image_dir = self.work_dir / "images"
        self.chunk_dir = self.work_dir / "chunks"
        self.chunk_dir.mkdir(parents=True, exist_ok=True)

    # -- steps ---------------------------------------------------------------

    def profile(self, doc: Document) -> Profile:
        path = self.work_dir / "profile.json"
        if path.exists():
            log(t("run.cached_profile", path=path))
            return Profile.from_dict(json.loads(path.read_text()))
        if self.engine is None:
            profile = self._profile_without_ai(doc)
        else:
            log(t("run.analyst", engine=self.engine.name))
            profile = agents.analyze(self.engine, doc)
        path.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
        return profile

    def _profile_without_ai(self, doc: Document) -> Profile:
        first, last = doc.pages[0].number, doc.pages[-1].number
        chapters = [Chapter(title, p, p) for n, title, p in doc.outline if n == 1 and first <= p <= last]
        chapters = agents.normalize_chapters(chapters, first, last)
        meta = doc.metadata
        return Profile(meta.get("title") or self.pdf.stem, [meta["author"]] if meta.get("author") else [],
                       "en", "", "", "", [], "", chapters)

    def convert_chapter(self, i: int, chapter: Chapter, doc: Document, profile: Profile) -> list[str] | None:
        if chapter.skip:
            return None
        pages = [p for p in doc.pages if chapter.start <= p.number <= chapter.end]
        fragments: list[str] = []
        for chunk in _chunks(pages, self.opt.pages_per_chunk):
            fragments.extend(self._convert_chunk(i, chapter, chunk, profile, fragments, chunk[0].number == chapter.start))
        log(t("run.chapter_done", i=i, title=chapter.title))
        return fragments

    def _convert_chunk(self, i, chapter, pages, profile, previous, chapter_start) -> list[str]:
        a, b = pages[0].number, pages[-1].number
        # The math mode changes the agent's output, so it is part of the cache key.
        cache = self.chunk_dir / f"ch{i:03d}_p{a:04d}-{b:04d}.{self.opt.math}.html"
        if cache.exists():
            return [cache.read_text()]

        if self.engine is None:
            xhtml = assembler.basic_conversion(pages)
            cache.write_text(xhtml)
            return [xhtml]

        def split() -> list[str]:
            half = len(pages) // 2
            first = self._convert_chunk(i, chapter, pages[:half], profile, previous, chapter_start)
            return first + self._convert_chunk(i, chapter, pages[half:], profile, previous + first, False)

        pdf_bytes = extractor.sub_pdf(self.pdf, a, b) if self.opt.vision else None
        if pdf_bytes and len(pdf_bytes) > MAX_CHUNK_PDF:
            if len(pages) > 1:
                return split()
            log(t("run.page_too_big", page=a))
            pdf_bytes = None

        asset_ids = {asset.id for p in pages for asset in p.assets}
        previous_end = previous[-1] if previous else ""
        try:
            xhtml = agents.layout(self.engine, profile, chapter, pages, pdf_bytes, previous_end, chapter_start,
                                  self.opt.math)
            for attempt in range(self.opt.reviews + 1):
                problems = verifier.check(xhtml, pages, asset_ids)
                if not problems:
                    break
                if attempt == self.opt.reviews:
                    log(t("run.warnings_left", a=a, b=b, n=attempt, problem=problems[0][:150]))
                    break
                log(t("run.reviewing", a=a, b=b, problem=problems[0][:120]))
                xhtml = agents.review(self.engine, profile, pages, pdf_bytes, xhtml, problems)
        except TruncatedResponse:
            if len(pages) == 1:
                raise
            log(t("run.split", a=a, b=b))
            return split()
        except RefusedResponse as e:
            log(t("run.refused", a=a, b=b, error=e))
            xhtml = assembler.basic_conversion(pages, t("run.basic_notice", a=a, b=b))

        cache.write_text(xhtml)
        return [xhtml]

    # -- main ----------------------------------------------------------------

    def run(self) -> None:
        log(t("run.extracting", name=self.pdf.name))
        doc = extractor.extract(self.pdf, self.image_dir, self.opt.page_range,
                                ExtractOptions(diagrams=self.opt.diagrams, math=self.opt.math))
        scanned = sum(p.no_text for p in doc.pages)
        log(t("run.extracted", pages=len(doc.pages), size=f"{doc.body_size:g}",
              images=sum(len(p.assets) for p in doc.pages))
            + (t("run.scanned", n=scanned) if scanned else ""))
        multi = sum(p.columns > 1 for p in doc.pages)
        if multi:
            log(t("run.columns", n=multi))
        if scanned and (self.engine is None or not self.opt.vision):
            log(t("run.scanned_no_vision"))

        profile = self.profile(doc)
        active = [c for c in profile.chapters if not c.skip]
        log(t("run.book", title=profile.title, n=len(active), subject=profile.subject or t("run.no_subject")))
        log(t("run.layout"))

        results: dict[int, list[str] | None] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.opt.parallel)) as pool:
            futures = {pool.submit(self.convert_chapter, i, c, doc, profile): i
                       for i, c in enumerate(profile.chapters, 1)}
            for future in as_completed(futures):
                results[futures[future]] = future.result()

        log(t("run.assembling"))
        final = []
        for i, chapter in enumerate(profile.chapters, 1):
            fragments = results.get(i)
            if not fragments:
                continue
            body = assembler.merge_chunks(fragments)
            final.append(assembler.prepare_chapter(chapter.title, i, body, "images", self.opt.syntax_highlight,
                                                   doc.asset_files()))

        cover = extractor.render_cover(self.pdf, self.work_dir / "cover.png")
        assembler.build_epub(profile, final, self.image_dir, cover, self.output, self.opt.syntax_highlight)
        log(t("run.done", path=self.output))
        if self.engine:
            self._report_usage()

    def _report_usage(self) -> None:
        u = self.engine.usage
        cost = u.cost(self.engine.model)
        if cost is None:
            cost_text = ""
        elif isinstance(self.engine, ClaudeCode):
            cost_text = t("run.cost_plan", cost=cost)
        else:
            cost_text = t("run.cost_api", cost=cost)
        log(t("run.usage", calls=u.calls, engine=self.engine.name, input=u.input,
              cached=u.cache_read, output=u.output, cost=cost_text))


def estimate(pdf: Path, options: Options) -> str:
    doc = pymupdf.open(pdf)
    first, last = options.page_range or (1, doc.page_count)
    last = min(last, doc.page_count)
    n = last - first + 1
    chars = sum(len(doc[i].get_text()) for i in range(first - 1, last))
    text_tokens = chars / 3.5
    # Each page sent as PDF also costs ~1,600 image tokens; output ≈ text + markup.
    input_tokens = text_tokens + (n * 1600 if options.vision else 0) + n * 150
    output_tokens = text_tokens * 1.25
    return t("run.estimate", pages=n, input=input_tokens / 1e6, output=output_tokens / 1e6)
