"""Pruebas del flujo completo con un Claude simulado (no consume API)."""

import json
import zipfile
from pathlib import Path

import pymupdf
import pytest

from pdftoepub import agentes, ensamblador, verificador
from pdftoepub.agentes import MARCA_CONTINUA
from pdftoepub.extractor import Pagina
from pdftoepub.orquestador import Conversor, Opciones


def crear_pdf(ruta: Path) -> None:
    doc = pymupdf.open()
    for n in range(1, 5):
        p = doc.new_page()
        p.insert_text((72, 30), "Guía práctica de Linux", fontsize=8)  # cabecera repetida
        p.insert_text((290, 820), str(n), fontsize=8)  # número de página
        y = 80
        if n in (1, 3):
            p.insert_text((72, y), f"Capítulo {(n + 1) // 2}", fontname="hebo", fontsize=20)
            y += 30
        p.insert_text((72, y), "El comando ls muestra el contenido de un directorio:", fontsize=10)
        p.insert_text((72, y + 20), f"$ ls -la /etc/pagina{n}", fontname="cour", fontsize=9)
        p.insert_text((72, y + 34), f"    echo \"hola {n}\" >> salida.log", fontname="cour", fontsize=9)
    doc.set_toc([[1, "Capítulo 1", 1], [1, "Capítulo 2", 3]])
    doc.save(ruta)


class ClaudeFalso:
    modelo = "falso"
    nombre = "falso"

    def __init__(self):
        self.llamadas = []
        self.consumo = type("C", (), {"llamadas": 0, "entrada": 0, "cache_leida": 0, "salida": 0,
                                      "coste": lambda self, m: None})()

    def pedir_json(self, sistema, contenido, esquema, **kw):
        self.llamadas.append("analista")
        return {"titulo": "Guía práctica de Linux", "autores": ["Ana"], "idioma": "es", "editorial": "",
                "descripcion": "", "tematica": "Linux", "lenguajes_codigo": ["console"], "notas_estilo": "",
                "capitulos": [{"titulo": "Capítulo 1", "pagina_inicio": 1, "pagina_fin": 2, "omitir": False},
                              {"titulo": "Capítulo 2", "pagina_inicio": 3, "pagina_fin": 9, "omitir": False}]}

    def pedir(self, sistema, contenido, **kw):
        texto = contenido[-1]["text"]
        if sistema == agentes.SISTEMA_REVISOR:
            self.llamadas.append("revisor")
            return self._bloque(texto, completo=True)
        self.llamadas.append("maquetador")
        # El primer intento olvida una línea de código para forzar al Revisor.
        return self._bloque(texto, completo=False)

    @staticmethod
    def _bloque(texto, completo):
        paginas = [int(l.split()[2]) for l in texto.splitlines() if l.startswith("=== Página")]
        partes = []
        for n in paginas:
            if n in (1, 3):
                partes.append(f"<h1>Capítulo {(n + 1) // 2}</h1>")
            partes.append("<p>El comando <code>ls</code> muestra el contenido de un directorio:</p>")
            codigo = f"$ ls -la /etc/pagina{n}\n"
            if completo or n != 1:
                codigo += f'    echo "hola {n}" &gt;&gt; salida.log'
            partes.append(f'<pre><code class="language-console">{codigo}</code></pre>')
        return "\n".join(partes)


def test_conversion_completa(tmp_path):
    pdf = tmp_path / "libro.pdf"
    crear_pdf(pdf)
    claude = ClaudeFalso()
    salida = tmp_path / "libro.epub"
    Conversor(pdf, salida, Opciones(paginas_por_bloque=1, vision=True), claude).ejecutar()

    assert claude.llamadas.count("analista") == 1
    assert "revisor" in claude.llamadas  # la línea perdida activó al Revisor

    with zipfile.ZipFile(salida) as z:
        nombres = z.namelist()
        cap1 = z.read("EPUB/cap001.xhtml").decode()
        nav = z.read("EPUB/nav.xhtml").decode()
    assert "EPUB/cap002.xhtml" in nombres
    assert cap1.count("<h1") == 1
    assert "hola 1" in cap1 and "pagina2" in cap1  # nada perdido
    assert '<span class="' in cap1  # resaltado de sintaxis
    assert "Capítulo 2" in nav

    # Segunda ejecución: todo sale de la caché, sin llamadas nuevas.
    claude2 = ClaudeFalso()
    Conversor(pdf, salida, Opciones(paginas_por_bloque=1), claude2).ejecutar()
    assert claude2.llamadas == []
    perfil = json.loads((tmp_path / "libro.trabajo" / "perfil.json").read_text())
    assert perfil["capitulos"][-1]["pagina_fin"] == 4  # el rango se normaliza al PDF real


def test_continuidad_entre_bloques():
    cuerpo = ensamblador.unir_bloques([
        '<p>Primera mitad de la</p><pre><code class="language-c">int main() {</code></pre>',
        f'{MARCA_CONTINUA}<pre><code class="language-c">    return 0;\n}}</code></pre><p>Fin.</p>',
        f'<ul><li>uno</li></ul>',
        f'{MARCA_CONTINUA}<li>dos</li><p>Tras la lista</p>',
    ])
    ns = ensamblador.NS
    pres = cuerpo.findall(f"{ns}pre")
    assert len(pres) == 1
    assert "".join(pres[0].itertext()) == "int main() {\n    return 0;\n}"
    assert len(cuerpo.find(f"{ns}ul")) == 2


def test_verificador_detecta_perdidas():
    pagina = Pagina(1, "Texto normal del párrafo con varias palabras importantes.\n¦ $ make install\n¦ $ make clean")
    ok = '<p>Texto normal del párrafo con varias palabras importantes.</p><pre><code>$ make install\n$ make clean</code></pre>'
    assert verificador.comprobar(ok, [pagina], set()) == []
    assert verificador.comprobar("<p>Texto</p>", [pagina], set())
    assert "mal formado" in verificador.comprobar("<p>sin cerrar", [pagina], set())[0]
    assert verificador.comprobar('<img src="falsa"/>' + ok, [pagina], set())


@pytest.mark.parametrize("entrada", ["<p>a &nbsp; b</p>", "<p>roto <b>x</p>"])
def test_reparacion(entrada):
    cuerpo = ensamblador._a_arbol(entrada)
    assert cuerpo.tag.endswith("div")


def test_motor_claude_code_con_ejecutable_falso(tmp_path, monkeypatch):
    """El motor Claude Code interpreta la salida stream-json de `claude -p`."""
    from pdftoepub.llm import ClaudeCode, ErrorMotor, RespuestaRechazada

    falso = tmp_path / "claude"
    falso.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "msg = json.loads(sys.stdin.readline())['message']['content'][-1]['text']\n"
        "res = {'type': 'result', 'subtype': 'success', 'is_error': False, 'stop_reason': 'end_turn',\n"
        "       'result': '<p>' + msg + '</p>', 'total_cost_usd': 0.01,\n"
        "       'usage': {'input_tokens': 10, 'output_tokens': 5}}\n"
        "if '--json-schema' in args: res['structured_output'] = {'ok': True}\n"
        "if msg == 'rechaza': res['stop_reason'] = 'refusal'\n"
        "if msg == 'falla': res.update(is_error=True, subtype='error_during_execution', result='sin sesión')\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init'}))\n"
        "print(json.dumps(res))\n"
    )
    falso.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")

    motor = ClaudeCode()
    assert motor.pedir("sistema", "hola") == "<p>hola</p>"
    assert motor.pedir_json("sistema", "x", {"type": "object"}) == {"ok": True}
    assert motor.consumo.llamadas == 2 and motor.consumo.coste("x") == pytest.approx(0.02)
    with pytest.raises(RespuestaRechazada):
        motor.pedir("sistema", "rechaza")
    with pytest.raises(ErrorMotor):
        motor.pedir("sistema", "falla")
