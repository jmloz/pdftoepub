"""Deterministic quality checks on every converted chunk.

If something fails, the chunk goes to the Reviewer agent together with the list of problems.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from lxml import etree, html

from .agents import CONTINUES_MARK
from .extractor import BOX_CLOSE, BOX_OPEN, CODE_MARK, MATH_CLOSE, MATH_OPEN, Page

XHTML_NS = "http://www.w3.org/1999/xhtml"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
COVERAGE_THRESHOLD = 0.93
MAX_MISSING_CODE = 0.03

_WORD = re.compile(r"\w{3,}", re.UNICODE)
_EQUIVALENTS = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "−": "-", " ": " "})


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_EQUIVALENTS)).strip()


def _source_text(pages: list[Page]) -> str:
    text = "\n".join(p.text for p in pages)
    text = re.sub(r"^\[H[\d.]+\] ", "", text, flags=re.M)
    text = text.replace("**", "").replace(CODE_MARK, "").replace(BOX_OPEN, "").replace(BOX_CLOSE, "")
    # Math-font text is often garbled in the PDF and becomes MathML or an image:
    # it is not required to appear literally.
    text = re.sub(f"{MATH_OPEN}.*?{MATH_CLOSE}", " ", text, flags=re.S)
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # rejoin words hyphenated at line end


LIGATURES = ("ffi", "ffl", "ff", "fi", "fl")


def _ligature_variants(words: Counter) -> Counter:
    """How output words look when a PDF loses its ligature glyphs on extraction.

    Fonts without a Unicode mapping for "fi", "ff", "ffi"… drop them or leave a gap:
    the agent writes "official" but the extracted source reads "oicial" or "o cial".
    """
    variants = Counter()
    for word, n in words.items():
        for lig in LIGATURES:
            start = word.find(lig)
            while start != -1:
                before, after = word[:start], word[start + len(lig):]
                for variant in (before + after, before, after):
                    if len(variant) >= 3 and variant != word:
                        variants[variant] += n
                start = word.find(lig, start + 1)
    return variants


def parse(fragment: str) -> etree._Element:
    """Strictly parse an XHTML fragment. Raises etree.XMLSyntaxError if malformed."""
    fragment = fragment.replace(CONTINUES_MARK, "")
    return etree.fromstring(f'<div xmlns="{XHTML_NS}">{_html_entities(fragment)}</div>'.encode())


def repair(fragment: str) -> etree._Element:
    """Last resort: the lenient HTML parser repairs broken markup."""
    tree = html.fragment_fromstring(fragment.replace(CONTINUES_MARK, ""), create_parent="div")
    xml = etree.tostring(tree, method="xml", encoding="unicode")
    xml = re.sub(r"^<div>", f'<div xmlns="{XHTML_NS}">', xml)
    return etree.fromstring(xml.encode())


_ENTITIES = {"nbsp": "#160", "mdash": "#8212", "ndash": "#8211", "hellip": "#8230", "copy": "#169",
             "reg": "#174", "trade": "#8482", "laquo": "#171", "raquo": "#187", "rarr": "#8594",
             "larr": "#8592", "times": "#215", "deg": "#176", "bull": "#8226", "middot": "#183"}


def _html_entities(text: str) -> str:
    """Translate named HTML entities (invalid in XML) into numeric ones."""
    return re.sub(r"&([a-zA-Z]+);", lambda m: f"&{_ENTITIES[m.group(1)]};" if m.group(1) in _ENTITIES else m.group(0), text)


def check(fragment: str, pages: list[Page], asset_ids: set[str]) -> list[str]:
    problems: list[str] = []
    try:
        tree = parse(fragment)
    except etree.XMLSyntaxError as e:
        return [f"Malformed XHTML: {e}"]

    output = normalize("".join(tree.itertext()))

    # Text coverage: are (almost) all of the original's words present?
    text_pages = [p for p in pages if not p.no_text]
    if text_pages:
        # NFKC turns ligature characters such as "ﬀ" (U+FB00) into plain letters.
        source_text = unicodedata.normalize("NFKC", _source_text(text_pages))
        source = Counter(w.lower() for w in _WORD.findall(source_text))
        target = Counter(w.lower() for w in _WORD.findall(output))
        target += _ligature_variants(target)
        total = sum(source.values())
        if total:
            covered = sum(min(n, target[w]) for w, n in source.items())
            coverage = covered / total
            if coverage < COVERAGE_THRESHOLD:
                missing = [w for w, n in source.most_common() if target[w] < n][:40]
                problems.append(
                    f"Only {coverage:.0%} of the original's words appear in the XHTML "
                    f"({COVERAGE_THRESHOLD:.0%} required). Missing or under-represented words: "
                    + ", ".join(missing)
                )

    # Code: every monospaced line of the original must be in the output.
    code_lines = [normalize(l) for p in pages for l in p.code_lines if len(l.strip()) > 3]
    if code_lines:
        lost = [l for l in code_lines if l not in output]
        if len(lost) > max(2, len(code_lines) * MAX_MISSING_CODE):
            sample = "\n".join(f"    {l}" for l in lost[:20])
            problems.append(f"{len(lost)} of {len(code_lines)} code lines are missing or altered. Examples:\n{sample}")

    # Images: only IDs of existing assets.
    for img in tree.iter(f"{{{XHTML_NS}}}img"):
        src = img.get("src", "")
        if src not in asset_ids:
            problems.append(f'<img src="{src}"> does not match any available asset.')

    return problems
