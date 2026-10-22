"""End-to-end tests with a fake Claude (no API or plan usage)."""

import json
import os
import zipfile
from pathlib import Path

import pymupdf
import pytest

from pdftoepub import agents, assembler, i18n, verifier
from pdftoepub.agents import CONTINUES_MARK
from pdftoepub.extractor import BOX_CLOSE, BOX_OPEN, MATH_CLOSE, MATH_OPEN, ExtractOptions, Page, extract
from pdftoepub.pipeline import Converter, Options


def make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for n in range(1, 5):
        p = doc.new_page()
        p.insert_text((72, 30), "Practical Linux Guide", fontsize=8)  # running header
        p.insert_text((290, 820), str(n), fontsize=8)  # page number
        y = 80
        if n in (1, 3):
            p.insert_text((72, y), f"Chapter {(n + 1) // 2}", fontname="hebo", fontsize=20)
            y += 30
        p.insert_text((72, y), "The ls command lists the contents of a directory:", fontsize=10)
        p.insert_text((72, y + 20), f"$ ls -la /etc/page{n}", fontname="cour", fontsize=9)
        p.insert_text((72, y + 34), f"    echo \"hello {n}\" >> output.log", fontname="cour", fontsize=9)
    doc.set_toc([[1, "Chapter 1", 1], [1, "Chapter 2", 3]])
    doc.save(path)


class FakeEngine:
    model = "fake"
    name = "fake"

    def __init__(self):
        self.calls = []
        self.usage = type("U", (), {"calls": 0, "input": 0, "cache_read": 0, "output": 0,
                                    "cost": lambda self, m: None})()

    def ask_json(self, system, content, schema, **kw):
        self.calls.append("analyst")
        return {"title": "Practical Linux Guide", "authors": ["Ann"], "language": "en", "publisher": "",
                "description": "", "subject": "Linux", "code_languages": ["console"], "style_notes": "",
                "chapters": [{"title": "Chapter 1", "start_page": 1, "end_page": 2, "skip": False},
                             {"title": "Chapter 2", "start_page": 3, "end_page": 9, "skip": False}]}

    def ask(self, system, content, **kw):
        text = content[-1]["text"]
        if system == agents.REVIEWER_SYSTEM:
            self.calls.append("reviewer")
            return self._chunk(text, complete=True)
        self.calls.append("layout")
        # The first attempt drops a code line on page 1 to trigger the Reviewer.
        return self._chunk(text, complete=False)

    @staticmethod
    def _chunk(text, complete):
        pages = [int(l.split()[2]) for l in text.splitlines() if l.startswith("=== Page")]
        parts = []
        for n in pages:
            if n in (1, 3):
                parts.append(f"<h1>Chapter {(n + 1) // 2}</h1>")
            parts.append("<p>The <code>ls</code> command lists the contents of a directory:</p>")
            code = f"$ ls -la /etc/page{n}\n"
            if complete or n != 1:
                code += f'    echo "hello {n}" &gt;&gt; output.log'
            parts.append(f'<pre><code class="language-console">{code}</code></pre>')
        return "\n".join(parts)


def test_full_conversion(tmp_path):
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    engine = FakeEngine()
    output = tmp_path / "book.epub"
    Converter(pdf, output, Options(pages_per_chunk=1, vision=True), engine).run()

    assert engine.calls.count("analyst") == 1
    assert "reviewer" in engine.calls  # the missing line triggered the Reviewer

    with zipfile.ZipFile(output) as z:
        names = z.namelist()
        ch1 = z.read("EPUB/ch001.xhtml").decode()
        nav = z.read("EPUB/nav.xhtml").decode()
    assert "EPUB/ch002.xhtml" in names
    assert ch1.count("<h1") == 1
    assert "hello 1" in ch1 and "page2" in ch1  # nothing lost
    assert '<span class="' in ch1  # syntax highlighting
    assert "Chapter 2" in nav

    # Second run: everything comes from the cache, no new calls.
    engine2 = FakeEngine()
    Converter(pdf, output, Options(pages_per_chunk=1), engine2).run()
    assert engine2.calls == []
    profile = json.loads((tmp_path / "book.work" / "profile.json").read_text())
    assert profile["chapters"][-1]["end_page"] == 4  # range is clamped to the real PDF


def test_continuity_between_chunks():
    body = assembler.merge_chunks([
        '<p>First half of the</p><pre><code class="language-c">int main() {</code></pre>',
        f'{CONTINUES_MARK}<pre><code class="language-c">    return 0;\n}}</code></pre><p>End.</p>',
        '<ul><li>one</li></ul>',
        f'{CONTINUES_MARK}<li>two</li><p>After the list</p>',
    ])
    ns = assembler.NS
    pres = body.findall(f"{ns}pre")
    assert len(pres) == 1
    assert "".join(pres[0].itertext()) == "int main() {\n    return 0;\n}"
    assert len(body.find(f"{ns}ul")) == 2


def test_verifier_detects_losses():
    page = Page(1, "Normal paragraph text with several important words.\n¦ $ make install\n¦ $ make clean")
    ok = '<p>Normal paragraph text with several important words.</p><pre><code>$ make install\n$ make clean</code></pre>'
    assert verifier.check(ok, [page], set()) == []
    assert verifier.check("<p>Text</p>", [page], set())
    assert "Malformed" in verifier.check("<p>unclosed", [page], set())[0]
    assert verifier.check('<img src="fake"/>' + ok, [page], set())


@pytest.mark.parametrize("fragment", ["<p>a &nbsp; b</p>", "<p>broken <b>x</p>"])
def test_repair(fragment):
    assert assembler.to_tree(fragment).tag.endswith("div")


def test_claude_code_engine_with_fake_executable(tmp_path, monkeypatch):
    """The Claude Code engine parses the stream-json output of `claude -p`."""
    from pdftoepub.llm import ClaudeCode, EngineError, RefusedResponse

    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "msg = json.loads(sys.stdin.readline())['message']['content'][-1]['text']\n"
        "res = {'type': 'result', 'subtype': 'success', 'is_error': False, 'stop_reason': 'end_turn',\n"
        "       'result': '<p>' + msg + '</p>', 'total_cost_usd': 0.01,\n"
        "       'usage': {'input_tokens': 10, 'output_tokens': 5}}\n"
        "if '--json-schema' in args: res['structured_output'] = {'ok': True}\n"
        "if msg == 'refuse': res['stop_reason'] = 'refusal'\n"
        "if msg == 'fail': res.update(is_error=True, subtype='error_during_execution', result='not logged in')\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init'}))\n"
        "print(json.dumps(res))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    engine = ClaudeCode()
    assert engine.ask("system", "hello") == "<p>hello</p>"
    assert engine.ask_json("system", "x", {"type": "object"}) == {"ok": True}
    assert engine.usage.calls == 2 and engine.usage.cost("x") == pytest.approx(0.02)
    with pytest.raises(RefusedResponse):
        engine.ask("system", "refuse")
    with pytest.raises(EngineError):
        engine.ask("system", "fail")


def test_language_detection(monkeypatch):
    for var in ("PDFTOEPUB_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    assert i18n.detect([]) == "en"
    assert i18n.detect(["book.pdf", "--lang", "es"]) == "es"
    assert i18n.detect(["--lang=es"]) == "es"
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert i18n.detect([]) == "es"
    monkeypatch.setenv("PDFTOEPUB_LANG", "en")
    assert i18n.detect([]) == "en"
    assert i18n.detect(["--lang", "fr"]) == "en"  # unsupported -> English


def test_every_message_has_both_languages():
    for key, texts in i18n.MESSAGES.items():
        assert set(texts) == set(i18n.LANGUAGES), key


# ---------------------------------------------------------------------------
# Complex layouts, vector diagrams and math
# ---------------------------------------------------------------------------

def make_complex_pdf(path: Path) -> None:
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 70), "Chapter 1. Two columns", fontname="hebo", fontsize=20)
    p.insert_textbox(pymupdf.Rect(72, 100, 290, 300),
                     "LEFT1 The left column starts here and continues with several lines of text. LEFT2 end.", fontsize=10)
    p.insert_textbox(pymupdf.Rect(310, 100, 530, 300),
                     "RIGHT1 The right column is read after the left one, with more lines. RIGHT2 end.", fontsize=10)
    p.draw_rect(pymupdf.Rect(70, 320, 530, 400), color=(0, 0, 0))
    p.insert_textbox(pymupdf.Rect(80, 330, 520, 395), "Note: this sidebar is framed.", fontsize=10)
    p.insert_text((200, 440), "E = m c 2", fontname="symb", fontsize=12)  # Symbol = math font
    p.insert_textbox(pymupdf.Rect(72, 460, 530, 520), "FULL Full-width body text below the formula.", fontsize=10)
    for i in range(10):  # a diagram made of separate strokes
        p.draw_line((100 + i * 30, 540), (130 + i * 25, 700), color=(0, 0, 1))
    p.draw_circle((300, 620), 60, color=(1, 0, 0))
    doc.save(path)


def test_reading_order_frames_and_math(tmp_path):
    pdf = tmp_path / "complex.pdf"
    make_complex_pdf(pdf)
    page = extract(pdf, tmp_path / "img").pages[0]
    text = page.text
    assert page.columns == 2
    order = [text.index(w) for w in ("LEFT1", "LEFT2", "RIGHT1", "RIGHT2", "Note:", "FULL")]
    assert order == sorted(order)  # left column fully before the right one
    assert f"{BOX_OPEN}\nNote: this sidebar is framed." in text and BOX_CLOSE in text
    assert f"{MATH_OPEN}E = m c 2{MATH_CLOSE}" in text


def test_diagrams_svg_by_default_png_optional(tmp_path):
    pdf = tmp_path / "complex.pdf"
    make_complex_pdf(pdf)
    svg = extract(pdf, tmp_path / "a").pages[0].assets
    png = extract(pdf, tmp_path / "b", options=ExtractOptions(diagrams="png")).pages[0].assets
    # One diagram (strokes merged), and the sidebar frame is not mistaken for one.
    assert [(a.kind, Path(a.file).suffix) for a in svg] == [("diagram", ".svg")]
    assert [(a.kind, Path(a.file).suffix) for a in png] == [("diagram", ".png")]
    assert svg[0].width > 250  # covers all the strokes, not just the circle
    assert pymupdf.open(tmp_path / "a" / svg[0].file).page_count == 1  # valid SVG


def test_math_image_mode_exports_formulas(tmp_path):
    pdf = tmp_path / "complex.pdf"
    make_complex_pdf(pdf)
    assets = extract(pdf, tmp_path / "img", options=ExtractOptions(math="image")).pages[0].assets
    assert [a.kind for a in assets].count("formula") == 1


def test_mathml_and_svg_in_epub(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "vec_p1_1.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    body = assembler.merge_chunks([
        '<h1>Physics</h1><p>Energy: <math alttext="E=mc^2"><mi>E</mi><mo>=</mo>'
        '<mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math></p>'
        '<figure><img src="vec_p1_1" alt="diagram"/></figure>'
    ])
    chapter = assembler.prepare_chapter("Physics", 1, body, "images", True, {"vec_p1_1": "vec_p1_1.svg"})
    assert chapter.has_math
    assert body.find(f".//{{{verifier.MATHML_NS}}}msup") is not None  # moved to the MathML namespace

    profile = agents.Profile("Book", [], "en", "", "", "", [], "", [])
    output = tmp_path / "book.epub"
    assembler.build_epub(profile, [chapter], tmp_path / "images", None, output, True)
    with zipfile.ZipFile(output) as z:
        opf = z.read("EPUB/content.opf").decode()
        xhtml = z.read("EPUB/ch001.xhtml").decode()
    assert 'properties="mathml"' in opf
    assert 'media-type="image/svg+xml"' in opf
    assert 'xmlns="http://www.w3.org/1998/Math/MathML"' in xhtml
    assert 'src="images/vec_p1_1.svg"' in xhtml


def test_layout_prompt_follows_math_mode():
    assert "MathML" in agents.layout_system("mathml") and "formula\" assets" not in agents.layout_system("mathml")
    assert "\"formula\" assets" in agents.layout_system("image")
    for mode in ("mathml", "image"):
        assert "{" not in agents.layout_system(mode).replace("{}", "")


def test_verifier_accepts_repaired_ligatures():
    # The PDF lost its "ffi"/"ff" ligature glyphs: the source reads "o cial", "dierent".
    page = Page(1, "This is an uno cial book. It shows a dierent and eective way to work with Java les.")
    fixed = "<p>This is an unofficial book. It shows a different and effective way to work with Java files.</p>"
    assert verifier.check(fixed, [page], set()) == []
    assert verifier.check("<p>This is a book.</p>", [page], set())  # real losses are still caught


def test_verifier_accepts_ligature_characters():
    page = Page(1, "Use a StringBuﬀer to build the ﬁnal string eﬃciently.")
    fixed = "<p>Use a <code>StringBuffer</code> to build the final string efficiently.</p>"
    assert verifier.check(fixed, [page], set()) == []


def test_progress_and_status(tmp_path, capsys):
    from pdftoepub.pipeline import format_duration, read_status

    i18n.set_language("en")
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    work = tmp_path / "book.work"
    assert "No conversion" in read_status(work)

    Converter(pdf, tmp_path / "book.epub", Options(pages_per_chunk=1), FakeEngine()).run()
    log = capsys.readouterr().err
    assert "[1/4 chunks · 25%" in log and "[4/4 chunks · 100%" in log
    progress = json.loads((work / "progress.json").read_text())
    assert progress["finished"] and progress["done"] == progress["total"] == 4
    assert read_status(work) == "Conversion finished (4 chunks)."

    # Interrupted conversion: resuming reports what was already done.
    (work / "progress.json").unlink()
    next(work.glob("chunks/ch002_*")).unlink()
    assert "3/4 chunks · 75%" in read_status(work, pages_per_chunk=1)  # estimated without progress.json
    Converter(pdf, tmp_path / "book.epub", Options(pages_per_chunk=1), FakeEngine()).run()
    assert "Resuming: 3 of 4 chunks" in capsys.readouterr().err

    assert format_duration(20) == "<1m" and format_duration(600) == "10m" and format_duration(4500) == "1h 15m"
