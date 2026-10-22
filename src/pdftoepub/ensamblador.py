"""Ensambla los fragmentos XHTML en un EPUB 3."""

from __future__ import annotations

import html as html_lib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ebooklib import epub
from lxml import etree
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .agentes import MARCA_CONTINUA, Perfil
from .extractor import MARCA_CODIGO, Pagina
from .verificador import XHTML_NS, parsear, reparar

NS = f"{{{XHTML_NS}}}"
ESTILO_PYGMENTS = "friendly"

CSS_BASE = """\
body { font-family: serif; line-height: 1.5; margin: 0 0.5em; }
h1 { font-size: 1.6em; margin: 1em 0 0.8em; page-break-before: always; line-height: 1.2; }
h2 { font-size: 1.3em; margin: 1.4em 0 0.5em; line-height: 1.25; }
h3 { font-size: 1.12em; margin: 1.2em 0 0.4em; }
h4 { font-size: 1em; margin: 1em 0 0.3em; font-style: italic; }
h2, h3, h4 { page-break-after: avoid; }
p { margin: 0.4em 0; text-align: justify; hyphens: auto; -epub-hyphens: auto; }
code, kbd, pre { font-family: "DejaVu Sans Mono", "Liberation Mono", Menlo, Consolas, monospace; }
code { font-size: 0.9em; hyphens: none; -epub-hyphens: none; }
pre {
  font-size: 0.78em; line-height: 1.35; text-align: left;
  white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word;
  background: #f5f5f3; border: 1px solid #ddd; border-left: 3px solid #999;
  padding: 0.6em 0.7em; margin: 0.8em 0; page-break-inside: avoid;
}
pre code { font-size: 1em; }
kbd { border: 1px solid #aaa; border-radius: 3px; padding: 0 0.25em; font-size: 0.85em; }
table { border-collapse: collapse; margin: 0.8em 0; font-size: 0.88em; width: 100%; }
th, td { border: 1px solid #bbb; padding: 0.25em 0.45em; text-align: left; vertical-align: top; }
th { background: #eee; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: 0.85em; font-style: italic; margin-top: 0.3em; }
aside { border: 1px solid #ccc; border-left: 4px solid #777; padding: 0.4em 0.8em; margin: 0.9em 0; background: #fafafa; }
aside.advertencia { border-left-color: #b33; }
aside.consejo { border-left-color: #3a3; }
aside.nota-pie { border: none; background: none; font-size: 0.85em; padding: 0; border-top: 1px solid #ccc; }
.titulo-aside { font-weight: bold; margin-top: 0; }
a.ref-nota { vertical-align: super; font-size: 0.75em; }
dt { font-weight: bold; margin-top: 0.5em; }
dd { margin-left: 1.5em; }
.aviso-conversion { color: #a00; font-style: italic; }
"""


@dataclass
class CapituloFinal:
    titulo: str
    archivo: str
    cuerpo: etree._Element
    secciones: list[tuple[int, str, str]] = field(default_factory=list)  # (nivel, id, título)


# ---------------------------------------------------------------------------
# Unión de bloques
# ---------------------------------------------------------------------------

def _a_arbol(fragmento: str) -> etree._Element:
    try:
        return parsear(fragmento)
    except etree.XMLSyntaxError:
        return reparar(fragmento)


def _anexar_contenido(destino: etree._Element, origen: etree._Element, separador: str, literal: bool = False) -> None:
    """Mueve el texto y los hijos de `origen` al final de `destino`.

    Con `literal` (código) no se toca ningún espacio: la sangría es contenido.
    """
    def unir(a: str, b: str) -> str:
        return a.rstrip("\n") + separador + b if literal else a.rstrip() + separador + b.lstrip()

    hijos = list(destino)
    texto = origen.text or ""
    if hijos:
        hijos[-1].tail = unir(hijos[-1].tail or "", texto)
    else:
        destino.text = unir(destino.text or "", texto)
    for hijo in origen:
        destino.append(hijo)


def unir_bloques(fragmentos: list[str]) -> etree._Element:
    cuerpo = etree.Element(f"{NS}div", nsmap={None: XHTML_NS})
    for fragmento in fragmentos:
        continua = fragmento.lstrip().startswith(MARCA_CONTINUA)
        nuevo = _a_arbol(fragmento)
        elementos = list(nuevo)
        if continua and elementos and len(cuerpo):
            ultimo, primero = cuerpo[-1], elementos[0]
            if ultimo.tag == primero.tag and primero.tag in (f"{NS}p", f"{NS}li"):
                _anexar_contenido(ultimo, primero, " ")
                elementos = elementos[1:]
            elif ultimo.tag == primero.tag == f"{NS}pre":
                c_ult, c_pri = ultimo.find(f"{NS}code"), primero.find(f"{NS}code")
                if c_ult is not None and c_pri is not None:
                    c_pri.text = (c_pri.text or "").lstrip("\n")
                    _anexar_contenido(c_ult, c_pri, "\n", literal=True)
                    elementos = elementos[1:]
            elif ultimo.tag in (f"{NS}ul", f"{NS}ol") and primero.tag == f"{NS}li":
                # Elementos sueltos que continúan la lista anterior.
                while elementos and elementos[0].tag == f"{NS}li":
                    ultimo.append(elementos.pop(0))
            elif ultimo.tag == primero.tag and primero.tag in (f"{NS}ul", f"{NS}ol", f"{NS}table", f"{NS}dl"):
                destino = ultimo.find(f"{NS}tbody") if primero.tag == f"{NS}table" else ultimo
                origen = primero.find(f"{NS}tbody") if primero.tag == f"{NS}table" else primero
                if destino is not None and origen is not None:
                    for hijo in list(origen):
                        if hijo.tag != f"{NS}thead":
                            destino.append(hijo)
                    elementos = elementos[1:]
        for el in elementos:
            cuerpo.append(el)
    return cuerpo


# ---------------------------------------------------------------------------
# Post-proceso de cada capítulo
# ---------------------------------------------------------------------------

def _texto_plano(el: etree._Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _lexer(nombre: str):
    alias = {"console": "console", "shell": "bash", "sh": "bash", "terminal": "console", "zsh": "zsh",
             "text": "text", "plaintext": "text", "txt": "text", "dockerfile": "docker"}
    try:
        return get_lexer_by_name(alias.get(nombre.lower(), nombre.lower()))
    except ClassNotFound:
        return None


def resaltar_codigo(cuerpo: etree._Element) -> None:
    formato = HtmlFormatter(nowrap=True)
    for code in cuerpo.iter(f"{NS}code"):
        padre = code.getparent()
        if padre is None or padre.tag != f"{NS}pre":
            continue
        clase = code.get("class", "")
        m = re.search(r"language-([\w+#.-]+)", clase)
        lexer = _lexer(m.group(1)) if m else None
        if lexer is None or lexer.name == "Text only":
            continue
        fuente = "".join(code.itertext())
        coloreado = highlight(fuente, lexer, formato).rstrip("\n")
        nuevo = etree.fromstring(f'<code xmlns="{XHTML_NS}">{coloreado}</code>'.encode())
        code.text = nuevo.text
        for hijo in list(code):
            code.remove(hijo)
        for hijo in nuevo:
            code.append(hijo)


def preparar_capitulo(titulo: str, indice: int, cuerpo: etree._Element, ruta_imagenes: str, resaltar: bool) -> CapituloFinal:
    archivo = f"cap{indice:03d}.xhtml"

    if cuerpo.find(f".//{NS}h1") is None:
        primero = cuerpo[0] if len(cuerpo) else None
        if primero is not None and primero.tag == f"{NS}h2" and _texto_plano(primero).lower() == titulo.lower():
            primero.tag = f"{NS}h1"
        else:
            h1 = etree.Element(f"{NS}h1")
            h1.text = titulo
            cuerpo.insert(0, h1)

    secciones = []
    for n, h in enumerate(cuerpo.iter(f"{NS}h1", f"{NS}h2", f"{NS}h3")):
        h.set("id", h.get("id") or f"s{indice}-{n}")
        secciones.append((int(h.tag[-1]), h.get("id"), _texto_plano(h)))

    for img in cuerpo.iter(f"{NS}img"):
        img.set("src", f"{ruta_imagenes}/{img.get('src')}.png")
        img.set("alt", img.get("alt") or "")

    if resaltar:
        resaltar_codigo(cuerpo)
    return CapituloFinal(titulo, archivo, cuerpo, secciones)


def conversion_basica(paginas: list[Pagina], aviso: str | None = None) -> str:
    """Conversión sin IA: párrafos y bloques de código a partir del texto anotado."""
    partes: list[str] = []
    if aviso:
        partes.append(f'<p class="aviso-conversion">{html_lib.escape(aviso)}</p>')
    for p in paginas:
        codigo: list[str] = []
        parrafo: list[str] = []

        def cerrar_parrafo():
            if parrafo:
                texto = re.sub(r"(\w)- (\w)", r"\1\2", " ".join(parrafo))
                partes.append(f"<p>{html_lib.escape(texto)}</p>")
                parrafo.clear()

        def cerrar_codigo():
            if codigo:
                partes.append(f'<pre><code class="language-text">{html_lib.escape(chr(10).join(codigo))}</code></pre>')
                codigo.clear()

        for linea in p.texto.splitlines():
            if linea.startswith(MARCA_CODIGO):
                cerrar_parrafo()
                codigo.append(linea[len(MARCA_CODIGO):])
                continue
            cerrar_codigo()
            if not linea.strip():
                cerrar_parrafo()
            elif m := re.match(r"\[T[\d.]+\] (.*)", linea):
                cerrar_parrafo()
                partes.append(f"<h2>{html_lib.escape(m.group(1))}</h2>")
            else:
                parrafo.append(linea.replace("**", ""))
        cerrar_parrafo()
        cerrar_codigo()
        for r in p.recursos:
            if r.tipo == "imagen":
                partes.append(f'<figure><img src="{r.id}" alt=""/></figure>')
    return "\n".join(partes)


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

def _cuerpo_html(cap: CapituloFinal) -> bytes:
    # ebooklib genera la cabecera (título, idioma, CSS) y conserva solo el <body>.
    cuerpo = etree.tostring(cap.cuerpo, encoding="unicode")
    cuerpo = re.sub(r"^<div[^>]*>|</div>$", "", cuerpo)
    return f"<html><body>{cuerpo}</body></html>".encode()


def _indice(caps: list[CapituloFinal]) -> list:
    toc = []
    for cap in caps:
        hijos = [epub.Link(f"{cap.archivo}#{id_}", titulo, f"{cap.archivo}-{id_}")
                 for nivel, id_, titulo in cap.secciones if nivel == 2 and titulo]
        if hijos:
            toc.append((epub.Section(cap.titulo, href=cap.archivo), hijos))
        else:
            toc.append(epub.Link(cap.archivo, cap.titulo, cap.archivo))
    return toc


def construir_epub(
    perfil: Perfil,
    capitulos: list[CapituloFinal],
    dir_imagenes: Path,
    ruta_portada: Path | None,
    destino: Path,
    resaltar: bool,
) -> None:
    libro = epub.EpubBook()
    libro.set_identifier(f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, perfil.titulo + ''.join(perfil.autores))}")
    libro.set_title(perfil.titulo)
    libro.set_language(perfil.idioma or "es")
    for autor in perfil.autores:
        libro.add_author(autor)
    if perfil.descripcion:
        libro.add_metadata("DC", "description", perfil.descripcion)
    if perfil.editorial:
        libro.add_metadata("DC", "publisher", perfil.editorial)
    if perfil.tematica:
        libro.add_metadata("DC", "subject", perfil.tematica)

    if ruta_portada and ruta_portada.exists():
        libro.set_cover("portada.png", ruta_portada.read_bytes())

    css = CSS_BASE
    if resaltar:
        css += "\n" + HtmlFormatter(style=ESTILO_PYGMENTS).get_style_defs("pre code")
        css = re.sub(r"pre code \{[^}]*background[^}]*\}", "", css)  # el fondo lo pone `pre`
    hoja = epub.EpubItem(uid="estilos", file_name="estilos/libro.css", media_type="text/css", content=css)
    libro.add_item(hoja)

    usadas: set[str] = set()
    items = []
    for cap in capitulos:
        for img in cap.cuerpo.iter(f"{NS}img"):
            usadas.add(Path(img.get("src")).name)
        item = epub.EpubHtml(title=cap.titulo, file_name=cap.archivo, lang=perfil.idioma)
        item.content = _cuerpo_html(cap)
        item.add_item(hoja)
        libro.add_item(item)
        items.append(item)

    for nombre in sorted(usadas):
        ruta = dir_imagenes / nombre
        if ruta.exists():
            libro.add_item(epub.EpubImage(uid=Path(nombre).stem, file_name=f"imagenes/{nombre}",
                                          media_type="image/png", content=ruta.read_bytes()))

    libro.toc = _indice(capitulos)
    libro.add_item(epub.EpubNcx())
    libro.add_item(epub.EpubNav())
    libro.spine = (["cover"] if ruta_portada else []) + ["nav"] + items
    epub.write_epub(str(destino), libro, {"epub3_pages": False})

