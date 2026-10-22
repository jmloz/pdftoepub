"""Extracción determinista del PDF (sin IA).

Convierte cada página en un texto anotado que conserva las pistas tipográficas
que los agentes necesitan para reconstruir el libro con fidelidad:

- Líneas en fuente monoespaciada  -> marcadas como código (`¦ `).
- Líneas con fuente mayor que el cuerpo -> candidatas a título (`[T<tamaño>]`).
- Imágenes rasterizadas y diagramas vectoriales -> exportados a PNG con un ID.
- Cabeceras / pies de página repetidos -> eliminados.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MARCA_CODIGO = "¦ "
MIN_LADO_IMAGEN = 48  # px; descarta iconos, viñetas y adornos
MIN_AREA_DIAGRAMA = 0.04  # fracción del área de la página
ZONA_MARGEN = 0.08  # franja superior/inferior donde viven cabeceras y pies

FUENTES_MONO = re.compile(
    r"mono|courier|consol|menlo|inconsolata|source\s?code|code|fira\s?mono|"
    r"dejavu\s?sans\s?mono|liberation\s?mono|lucida\s?console|cmtt|sfmono|"
    r"roboto\s?mono|ubuntu\s?mono|jetbrains|hack|andale|letter\s?gothic|ocr",
    re.IGNORECASE,
)


@dataclass
class Recurso:
    id: str
    archivo: str  # nombre dentro de la carpeta de imágenes
    pagina: int
    tipo: str  # "imagen" | "diagrama"
    ancho: int
    alto: int


@dataclass
class Pagina:
    numero: int  # 1-indexado
    texto: str  # texto anotado
    recursos: list[Recurso] = field(default_factory=list)
    sin_texto: bool = False  # probablemente escaneada

    @property
    def lineas_codigo(self) -> list[str]:
        return [l[len(MARCA_CODIGO):] for l in self.texto.splitlines() if l.startswith(MARCA_CODIGO)]


@dataclass
class Documento:
    ruta: Path
    num_paginas: int
    metadatos: dict
    indice_pdf: list[tuple[int, str, int]]  # (nivel, título, página)
    tamano_cuerpo: float
    paginas: list[Pagina]
    candidatos_titulo: list[tuple[int, float, str]]  # (página, tamaño, texto)


def _es_mono(span: dict) -> bool:
    return bool(span["flags"] & pymupdf.TEXT_FONT_MONOSPACED) or bool(FUENTES_MONO.search(span["font"]))


def _normalizar_margen(texto: str) -> str:
    return re.sub(r"\d+", "#", texto.strip().lower())


def _lineas(pagina: pymupdf.Page) -> list[dict]:
    """Devuelve las líneas de texto de la página con su información tipográfica."""
    datos = pagina.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    alto = pagina.rect.height
    lineas = []
    for bloque in datos["blocks"]:
        if bloque.get("type") != 0:
            continue
        for linea in bloque["lines"]:
            spans = [s for s in linea["spans"] if s["text"].strip()]
            if not spans:
                continue
            texto = "".join(s["text"] for s in linea["spans"]).rstrip()
            caracteres = sum(len(s["text"]) for s in spans)
            mono = sum(len(s["text"]) for s in spans if _es_mono(s)) / caracteres > 0.6
            negrita = all(s["flags"] & pymupdf.TEXT_FONT_BOLD for s in spans)
            tamano = max(s["size"] for s in spans)
            y0, y1 = linea["bbox"][1], linea["bbox"][3]
            lineas.append(
                {
                    "texto": texto,
                    "x0": linea["bbox"][0],
                    "y0": y0,
                    "mono": mono,
                    "negrita": negrita,
                    "tamano": round(tamano, 1),
                    "margen": y1 < alto * ZONA_MARGEN or y0 > alto * (1 - ZONA_MARGEN),
                    "bloque": bloque["number"],
                }
            )
    return lineas


def _detectar_margenes_repetidos(todas: list[list[dict]]) -> set[str]:
    """Textos que aparecen en el margen de muchas páginas: cabeceras, pies, nº de página."""
    conteo = Counter()
    for lineas in todas:
        conteo.update({_normalizar_margen(l["texto"]) for l in lineas if l["margen"]})
    umbral = max(3, len(todas) * 0.15)
    repetidos = {t for t, n in conteo.items() if n >= umbral}
    repetidos.add("#")  # números de página sueltos
    return repetidos


def _exportar_recursos(doc: pymupdf.Document, pagina: pymupdf.Page, dir_img: Path) -> list[Recurso]:
    recursos: list[Recurso] = []
    n = pagina.number + 1
    vistos: set[int] = set()

    for i, info in enumerate(pagina.get_images(full=True)):
        xref = info[0]
        if xref in vistos:
            continue
        vistos.add(xref)
        try:
            pix = pymupdf.Pixmap(doc, xref)
            if pix.width < MIN_LADO_IMAGEN or pix.height < MIN_LADO_IMAGEN:
                continue
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            rid = f"img_p{n}_{i + 1}"
            archivo = f"{rid}.png"
            pix.save(dir_img / archivo)
            recursos.append(Recurso(rid, archivo, n, "imagen", pix.width, pix.height))
        except Exception:
            continue

    # Diagramas vectoriales: se rasterizan para no perderlos. El agente decide si
    # son un diagrama real o simples bordes de tabla / recuadros de código.
    area_pagina = pagina.rect.width * pagina.rect.height
    try:
        grupos = pagina.cluster_drawings()
    except Exception:
        grupos = []
    for i, rect in enumerate(grupos):
        if rect.width * rect.height < area_pagina * MIN_AREA_DIAGRAMA:
            continue
        rid = f"vec_p{n}_{i + 1}"
        archivo = f"{rid}.png"
        pix = pagina.get_pixmap(clip=rect + (-4, -4, 4, 4), dpi=200)
        pix.save(dir_img / archivo)
        recursos.append(Recurso(rid, archivo, n, "diagrama", pix.width, pix.height))
    return recursos


def _anotar(lineas: list[dict], repetidos: set[str], tamano_cuerpo: float) -> str:
    salida: list[str] = []
    bloque_anterior = None
    x_min_codigo = min((l["x0"] for l in lineas if l["mono"]), default=0)
    for l in lineas:
        if l["margen"] and _normalizar_margen(l["texto"]) in repetidos:
            continue
        if bloque_anterior is not None and l["bloque"] != bloque_anterior and not l["mono"]:
            salida.append("")  # separa párrafos
        bloque_anterior = l["bloque"]
        if l["mono"]:
            # Reconstruye la sangría del código a partir de la posición horizontal.
            # (ancho de un carácter monoespaciado ≈ 0.6 × tamaño de letra)
            sangria = max(0, round((l["x0"] - x_min_codigo) / (l["tamano"] * 0.6)))
            salida.append(MARCA_CODIGO + " " * sangria + l["texto"])
        elif l["tamano"] >= tamano_cuerpo * 1.15:
            salida.append(f"[T{l['tamano']:g}] {l['texto'].strip()}")
        elif l["negrita"]:
            salida.append(f"**{l['texto'].strip()}**")
        else:
            salida.append(l["texto"].strip())
    return "\n".join(salida).strip()


def extraer(ruta_pdf: Path, dir_img: Path, rango: tuple[int, int] | None = None) -> Documento:
    dir_img.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(ruta_pdf)
    if doc.needs_pass:
        raise ValueError("El PDF está protegido con contraseña.")
    inicio, fin = rango or (1, doc.page_count)
    fin = min(fin, doc.page_count)
    indices = range(inicio - 1, fin)

    todas = [_lineas(doc[i]) for i in indices]
    tamanos = Counter()
    for lineas in todas:
        for l in lineas:
            if not l["mono"]:
                tamanos[l["tamano"]] += len(l["texto"])
    tamano_cuerpo = tamanos.most_common(1)[0][0] if tamanos else 10.0
    repetidos = _detectar_margenes_repetidos(todas)

    paginas: list[Pagina] = []
    candidatos: list[tuple[int, float, str]] = []
    for i, lineas in zip(indices, todas):
        texto = _anotar(lineas, repetidos, tamano_cuerpo)
        recursos = _exportar_recursos(doc, doc[i], dir_img)
        paginas.append(Pagina(i + 1, texto, recursos, sin_texto=len(texto) < 20))
        for l in lineas:
            if l["tamano"] >= tamano_cuerpo * 1.3 and not l["mono"] and len(l["texto"].strip()) > 1:
                candidatos.append((i + 1, l["tamano"], l["texto"].strip()[:120]))

    return Documento(
        ruta=ruta_pdf,
        num_paginas=doc.page_count,
        metadatos={k: v for k, v in (doc.metadata or {}).items() if v},
        indice_pdf=[(nivel, titulo, pag) for nivel, titulo, pag in doc.get_toc(simple=True)],
        tamano_cuerpo=tamano_cuerpo,
        paginas=paginas,
        candidatos_titulo=candidatos,
    )


def subpdf(ruta_pdf: Path, primera: int, ultima: int) -> bytes:
    """Bytes de un PDF que contiene solo las páginas [primera, ultima] (1-indexado)."""
    origen = pymupdf.open(ruta_pdf)
    nuevo = pymupdf.open()
    nuevo.insert_pdf(origen, from_page=primera - 1, to_page=ultima - 1)
    return nuevo.tobytes(garbage=3, deflate=True)


def portada(ruta_pdf: Path, destino: Path) -> Path:
    pagina = pymupdf.open(ruta_pdf)[0]
    pagina.get_pixmap(dpi=150).save(destino)
    return destino
