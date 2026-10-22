"""End-to-end tests with a fake Claude (no API or plan usage)."""

import json
import os
import zipfile
from pathlib import Path

import pymupdf
import pytest

from pdftoepub import agents, assembler, i18n, verifier
from pdftoepub.agents import CONTINUES_MARK
from pdftoepub.extractor import Page
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
