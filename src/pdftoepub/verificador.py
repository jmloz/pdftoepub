"""Comprobaciones deterministas de calidad sobre cada bloque convertido.

Si algo falla, el bloque pasa al agente Revisor junto con la lista de problemas.
"""

from __future__ import annotations

import re
from collections import Counter

from lxml import etree, html

from .agentes import MARCA_CONTINUA
from .extractor import Pagina

XHTML_NS = "http://www.w3.org/1999/xhtml"
UMBRAL_COBERTURA = 0.93
MAX_CODIGO_PERDIDO = 0.03

_PALABRA = re.compile(r"\w{3,}", re.UNICODE)
_EQUIVALENCIAS = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "−": "-", " ": " "})


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.translate(_EQUIVALENCIAS)).strip()


def _texto_fuente(paginas: list[Pagina]) -> str:
    texto = "\n".join(p.texto for p in paginas)
    texto = re.sub(r"^\[T[\d.]+\] ", "", texto, flags=re.M)
    texto = texto.replace("**", "").replace("¦ ", "")
    return re.sub(r"(\w)-\n(\w)", r"\1\2", texto)  # une palabras cortadas a final de línea


def parsear(fragmento: str) -> etree._Element:
    """Parsea un fragmento XHTML de forma estricta. Lanza etree.XMLSyntaxError si está mal formado."""
    fragmento = fragmento.replace(MARCA_CONTINUA, "")
    return etree.fromstring(f'<div xmlns="{XHTML_NS}">{_entidades_html(fragmento)}</div>'.encode())


def reparar(fragmento: str) -> etree._Element:
    """Último recurso: el parser HTML tolerante repara el marcado roto."""
    arbol = html.fragment_fromstring(fragmento.replace(MARCA_CONTINUA, ""), create_parent="div")
    xml = etree.tostring(arbol, method="xml", encoding="unicode")
    xml = re.sub(r"^<div>", f'<div xmlns="{XHTML_NS}">', xml)
    return etree.fromstring(xml.encode())


_ENTIDADES = {"nbsp": "#160", "mdash": "#8212", "ndash": "#8211", "hellip": "#8230", "copy": "#169",
              "reg": "#174", "trade": "#8482", "laquo": "#171", "raquo": "#187", "rarr": "#8594",
              "larr": "#8592", "times": "#215", "deg": "#176", "bull": "#8226", "middot": "#183"}


def _entidades_html(texto: str) -> str:
    """Traduce entidades HTML con nombre (no válidas en XML) a numéricas."""
    return re.sub(r"&([a-zA-Z]+);", lambda m: f"&{_ENTIDADES[m.group(1)]};" if m.group(1) in _ENTIDADES else m.group(0), texto)


def comprobar(fragmento: str, paginas: list[Pagina], ids_recursos: set[str]) -> list[str]:
    problemas: list[str] = []
    try:
        arbol = parsear(fragmento)
    except etree.XMLSyntaxError as e:
        return [f"XHTML mal formado: {e}"]

    salida = normalizar("".join(arbol.itertext()))

    # Cobertura del texto: ¿están (casi) todas las palabras del original?
    paginas_con_texto = [p for p in paginas if not p.sin_texto]
    if paginas_con_texto:
        fuente = Counter(w.lower() for w in _PALABRA.findall(_texto_fuente(paginas_con_texto)))
        destino = Counter(w.lower() for w in _PALABRA.findall(salida))
        total = sum(fuente.values())
        if total:
            cubiertas = sum(min(n, destino[w]) for w, n in fuente.items())
            cobertura = cubiertas / total
            if cobertura < UMBRAL_COBERTURA:
                faltan = [w for w, n in fuente.most_common() if destino[w] < n][:40]
                problemas.append(
                    f"Solo el {cobertura:.0%} de las palabras del original aparecen en el XHTML "
                    f"(se exige {UMBRAL_COBERTURA:.0%}). Palabras que faltan o aparecen menos veces: "
                    + ", ".join(faltan)
                )

    # Código: cada línea en monoespaciada del original debe estar en la salida.
    lineas_codigo = [normalizar(l) for p in paginas for l in p.lineas_codigo if len(l.strip()) > 3]
    if lineas_codigo:
        perdidas = [l for l in lineas_codigo if l not in salida]
        if len(perdidas) > max(2, len(lineas_codigo) * MAX_CODIGO_PERDIDO):
            muestra = "\n".join(f"    {l}" for l in perdidas[:20])
            problemas.append(
                f"Faltan o están alteradas {len(perdidas)} de {len(lineas_codigo)} líneas de código. Ejemplos:\n{muestra}"
            )

    # Imágenes: solo IDs de recursos existentes.
    for img in arbol.iter(f"{{{XHTML_NS}}}img"):
        src = img.get("src", "")
        if src not in ids_recursos:
            problemas.append(f'<img src="{src}"> no corresponde a ningún recurso disponible.')

    return problemas
