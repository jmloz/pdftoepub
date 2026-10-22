"""Los agentes de IA del conversor.

1. Analista   -> entiende el libro: metadatos, tipo, lenguajes, estructura de capítulos.
2. Maquetador -> convierte cada bloque de páginas en XHTML semántico (código, tablas, notas…).
3. Revisor    -> corrige un bloque cuando las comprobaciones automáticas detectan pérdidas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .extractor import MARCA_CODIGO, Documento, Pagina, Recurso
from .llm import Motor, bloque_pdf

MARCA_CONTINUA = "<!--CONTINUA-->"

# ---------------------------------------------------------------------------
# 1. Analista
# ---------------------------------------------------------------------------

SISTEMA_ANALISTA = """\
Eres el agente Analista de un conversor de libros PDF a EPUB. Recibes la información \
estructural de un libro y decides cómo dividirlo en capítulos para el EPUB.

Reglas:
- Cada capítulo del EPUB será un archivo independiente. Usa como capítulos las divisiones \
de primer nivel reales del libro (prefacio, capítulos, apéndices). Las secciones internas se \
convertirán en subtítulos dentro del capítulo, no las incluyas como capítulos.
- Los capítulos deben cubrir todo el rango de páginas de forma contigua y sin solaparse: \
pagina_inicio del siguiente = pagina_fin del anterior + 1. No dejes huecos.
- Si el PDF trae un índice (marcadores), confía en él pero corrige títulos rotos o niveles mal asignados.
- Si no lo trae, deduce los capítulos de los candidatos a título (texto grande) y de las páginas de muestra.
- Marca omitir=true SOLO para páginas que no aportan nada en un EPUB: la tabla de contenidos \
impresa (el EPUB genera la suya) y páginas en blanco. Nunca omitas contenido real; el índice \
alfabético final sí puede omitirse porque sus números de página no tienen sentido en un EPUB.
- idioma: código BCP 47 del idioma del texto del libro (p. ej. "es", "en").
- lenguajes_codigo: lenguajes de programación, shells o formatos que aparecen en el libro \
(p. ej. "bash", "python", "c", "yaml", "sql", "console" para sesiones de terminal con prompt).
- notas_estilo: convenciones del libro que el maquetador debe respetar (cómo marca los \
prompts de shell, cajas de "Nota"/"Advertencia", convenciones tipográficas, etc.).
"""

ESQUEMA_ANALISTA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["titulo", "autores", "idioma", "editorial", "descripcion", "tematica",
                 "lenguajes_codigo", "notas_estilo", "capitulos"],
    "properties": {
        "titulo": {"type": "string"},
        "autores": {"type": "array", "items": {"type": "string"}},
        "idioma": {"type": "string"},
        "editorial": {"type": "string"},
        "descripcion": {"type": "string"},
        "tematica": {"type": "string"},
        "lenguajes_codigo": {"type": "array", "items": {"type": "string"}},
        "notas_estilo": {"type": "string"},
        "capitulos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["titulo", "pagina_inicio", "pagina_fin", "omitir"],
                "properties": {
                    "titulo": {"type": "string"},
                    "pagina_inicio": {"type": "integer"},
                    "pagina_fin": {"type": "integer"},
                    "omitir": {"type": "boolean"},
                },
            },
        },
    },
}


@dataclass
class Capitulo:
    titulo: str
    inicio: int
    fin: int
    omitir: bool = False


@dataclass
class Perfil:
    titulo: str
    autores: list[str]
    idioma: str
    editorial: str
    descripcion: str
    tematica: str
    lenguajes_codigo: list[str]
    notas_estilo: str
    capitulos: list[Capitulo]

    @classmethod
    def desde_dict(cls, d: dict) -> "Perfil":
        caps = [Capitulo(c["titulo"], c["pagina_inicio"], c["pagina_fin"], c["omitir"]) for c in d["capitulos"]]
        return cls(**{**d, "capitulos": caps})

    def a_dict(self) -> dict:
        d = dict(self.__dict__)
        d["capitulos"] = [
            {"titulo": c.titulo, "pagina_inicio": c.inicio, "pagina_fin": c.fin, "omitir": c.omitir}
            for c in self.capitulos
        ]
        return d

    def resumen(self) -> str:
        return (
            f"Título: {self.titulo}\nAutores: {', '.join(self.autores)}\nIdioma: {self.idioma}\n"
            f"Temática: {self.tematica}\nLenguajes de código: {', '.join(self.lenguajes_codigo) or 'ninguno'}\n"
            f"Convenciones del libro: {self.notas_estilo}"
        )


def _normalizar_capitulos(capitulos: list[Capitulo], primera: int, ultima: int) -> list[Capitulo]:
    """Garantiza cobertura contigua y sin solapes, pase lo que pase con la respuesta del modelo."""
    caps = sorted((c for c in capitulos if c.fin >= c.inicio), key=lambda c: c.inicio)
    if not caps:
        return [Capitulo("Contenido", primera, ultima)]
    caps[0].inicio = primera
    for anterior, siguiente in zip(caps, caps[1:]):
        anterior.fin = siguiente.inicio - 1
    caps[-1].fin = ultima
    return [c for c in caps if c.fin >= c.inicio]


def analizar(claude: Motor, doc: Documento, paginas_muestra: int = 12) -> Perfil:
    primera, ultima = doc.paginas[0].numero, doc.paginas[-1].numero
    muestra = "\n\n".join(
        f"=== Página {p.numero} ===\n{p.texto[:2500]}" for p in doc.paginas[:paginas_muestra]
    )
    candidatos = "\n".join(f"p.{pag} [{tam:g}pt] {txt}" for pag, tam, txt in doc.candidatos_titulo[:600])
    indice = "\n".join(f"{'  ' * (n - 1)}- {t} (p.{p})" for n, t, p in doc.indice_pdf) or "(el PDF no trae marcadores)"
    contenido = f"""\
Rango de páginas a convertir: {primera}–{ultima} (el PDF tiene {doc.num_paginas}).
Tamaño de letra del cuerpo: {doc.tamano_cuerpo:g}pt.
Metadatos del PDF: {json.dumps(doc.metadatos, ensure_ascii=False)}

## Marcadores del PDF
{indice}

## Candidatos a título (texto grande) por página
{candidatos or '(ninguno)'}

## Primeras páginas (texto anotado: '¦ ' = línea en monoespaciada, [Tn] = título de n pt)
{muestra}
"""
    datos = claude.pedir_json(SISTEMA_ANALISTA, contenido, ESQUEMA_ANALISTA)
    perfil = Perfil.desde_dict(datos)
    perfil.capitulos = _normalizar_capitulos(perfil.capitulos, primera, ultima)
    return perfil


# ---------------------------------------------------------------------------
# 2. Maquetador
# ---------------------------------------------------------------------------

SISTEMA_MAQUETADOR = """\
Eres el agente Maquetador de un conversor de libros PDF a EPUB, especializado en libros \
técnicos (programación, Linux, redes, comandos). Conviertes un bloque de páginas en XHTML \
semántico y limpio para un lector de libros electrónicos.

Recibes: el PDF de esas páginas (para ver la maquetación real) y el texto extraído y \
anotado de esas mismas páginas. Usa el texto extraído como fuente literal y el PDF para \
entender la estructura (qué es título, tabla, nota, código, pie de figura…).

PRINCIPIO FUNDAMENTAL: fidelidad total. No resumas, no parafrasees, no traduzcas, no \
añadas explicaciones ni omitas nada del contenido. Tu trabajo es reestructurar, no reescribir.

Qué debes hacer:
- Reunir las líneas partidas en párrafos completos y unir las palabras cortadas con guion a \
final de línea (“progra-\\nmación” → “programación”), salvo guiones que sí formen parte de la \
palabra o del código.
- Eliminar artefactos del PDF: cabeceras y pies repetidos, números de página, marcas de corte.
- Títulos: <h1> para el título del capítulo (solo si aparece en estas páginas), <h2>, <h3>, \
<h4> para las secciones según su jerarquía.
- Código, comandos y salidas de terminal: SIEMPRE en <pre><code class="language-XXX">…</code></pre> \
copiados carácter por carácter, respetando sangría, espacios, mayúsculas, comillas rectas \
(nunca tipográficas), guiones dobles (--opcion, nunca –), barras invertidas y saltos de línea. \
XXX es el lenguaje (bash, console, python, c, yaml, json, sql, text…). Usa "console" para \
sesiones de terminal con prompt ($, #, >>>) seguidas de su salida. Las líneas marcadas con \
'¦ ' en el texto extraído están en fuente monoespaciada: quita la marca '¦ ' y respeta la sangría \
que viene después. Si una línea de código se partió por el ancho de página (lo verás en el PDF), \
vuelve a unirla; si el libro usa '\\' o '↩' como continuación, consérvalo.
- Código en línea (nombres de comandos, archivos, opciones, variables dentro de un párrafo): <code>.
- Tablas: <table> con <thead>/<tbody>, <th> y <td>; conserva todas las celdas.
- Listas: <ul>/<ol>/<li>; listas de definición (opción → descripción) con <dl><dt><dd>.
- Notas, consejos, advertencias y recuadros: <aside class="nota|consejo|advertencia|recuadro"> \
con su título en <p class="titulo-aside">.
- Énfasis: <em> y <strong> donde el original use cursiva o negrita (**texto** en el texto extraído).
- Teclas: <kbd>Ctrl</kbd>+<kbd>C</kbd>.
- Imágenes: se te da una lista de recursos con ID. Para insertar uno usa exactamente \
<figure><img src="ID" alt="descripción breve y útil"/><figcaption>pie original</figcaption></figure>. \
Los recursos "diagrama" son zonas con dibujo vectorial: úsalos solo si en el PDF son de verdad \
un diagrama, gráfico o figura; ignóralos si son bordes de tabla, recuadros de código o adornos \
(en ese caso reconstruye la tabla o el código como texto).
- Si una página no tiene texto extraído (escaneada), transcríbela desde el PDF.
- Notas al pie: colócalas al final del bloque en <aside class="nota-pie" id="np-N"> y \
enlázalas con <a href="#np-N" class="ref-nota">N</a>.
- Referencias cruzadas tipo “véase la página 42”: conserva el texto tal cual.

Continuidad entre bloques:
- Se te da el final del bloque anterior. Si estas páginas empiezan a mitad de un párrafo, de una \
lista o de un bloque de código que venía de la página anterior, empieza tu respuesta EXACTAMENTE \
con {continua} seguido del elemento que continúa (<p>…</p>, <pre><code …>…</code></pre>, \
<li>…</li>) solo con la parte que falta; el ensamblador lo unirá con el anterior.
- No repitas contenido que ya estaba en el bloque anterior.

Formato de respuesta: solo el fragmento XHTML (sin <html>, <head> ni <body>, sin Markdown, \
sin ```), bien formado: etiquetas cerradas, <br/> e <img …/> autocerrados, y &amp; &lt; &gt; \
escapados (especialmente dentro de <code>).
""".replace("{continua}", MARCA_CONTINUA)


def _texto_paginas(paginas: list[Pagina]) -> str:
    partes = []
    for p in paginas:
        aviso = " (sin texto extraíble: transcribe desde el PDF)" if p.sin_texto else ""
        partes.append(f"=== Página {p.numero}{aviso} ===\n{p.texto}")
    return "\n\n".join(partes)


def _lista_recursos(recursos: list[Recurso]) -> str:
    if not recursos:
        return "(ninguno)"
    return "\n".join(f"- {r.id}: {r.tipo} de la página {r.pagina}, {r.ancho}x{r.alto}px" for r in recursos)


def maquetar(
    claude: Motor,
    perfil: Perfil,
    capitulo: Capitulo,
    paginas: list[Pagina],
    pdf_bytes: bytes | None,
    final_anterior: str,
    es_inicio_capitulo: bool,
) -> str:
    recursos = [r for p in paginas for r in p.recursos]
    texto = f"""\
## Libro
{perfil.resumen()}

## Capítulo actual
"{capitulo.titulo}" (páginas {capitulo.inicio}–{capitulo.fin}). \
Este bloque son las páginas {paginas[0].numero}–{paginas[-1].numero}. \
{"Es el comienzo del capítulo: abre con su <h1>." if es_inicio_capitulo else "Es la continuación del capítulo: no repitas el <h1>."}

## Final del bloque anterior
{final_anterior[-1500:] if final_anterior else "(no hay: este es el primer bloque del capítulo)"}

## Recursos gráficos disponibles
{_lista_recursos(recursos)}

## Texto extraído y anotado
('¦ ' = línea en fuente monoespaciada; [Tn] = línea con letra de n pt, probable título; **x** = negrita)

{_texto_paginas(paginas)}
"""
    contenido: list[dict] = []
    if pdf_bytes:
        contenido.append(bloque_pdf(pdf_bytes))
    contenido.append({"type": "text", "text": texto})
    return limpiar_salida(claude.pedir(SISTEMA_MAQUETADOR, contenido))


def limpiar_salida(xhtml: str) -> str:
    xhtml = xhtml.strip()
    if xhtml.startswith("```"):
        xhtml = xhtml.split("\n", 1)[1] if "\n" in xhtml else ""
        xhtml = xhtml.rsplit("```", 1)[0]
    return xhtml.strip()


# ---------------------------------------------------------------------------
# 3. Revisor
# ---------------------------------------------------------------------------

SISTEMA_REVISOR = """\
Eres el agente Revisor de un conversor de libros PDF a EPUB. Otro agente convirtió unas \
páginas a XHTML y las comprobaciones automáticas han detectado problemas. Tu tarea es \
devolver el fragmento XHTML corregido y completo.

- Compara el XHTML con el texto original y el PDF: recupera todo lo que falte (párrafos, \
líneas de código, celdas de tabla, notas) en su posición correcta.
- El código debe ser idéntico carácter por carácter al original, dentro de \
<pre><code class="language-XXX">.
- No resumas ni reescribas lo que ya está bien; corrige solo lo necesario.
- Mantén la marca {continua} al principio si la tenía.
- Responde solo con el fragmento XHTML corregido, bien formado, sin Markdown.
""".replace("{continua}", MARCA_CONTINUA)


def revisar(
    claude: Motor,
    perfil: Perfil,
    paginas: list[Pagina],
    pdf_bytes: bytes | None,
    xhtml: str,
    problemas: list[str],
) -> str:
    texto = f"""\
## Libro
{perfil.resumen()}

## Problemas detectados
{chr(10).join('- ' + p for p in problemas)}

## Texto original anotado de las páginas {paginas[0].numero}–{paginas[-1].numero}
('{MARCA_CODIGO}' = línea en fuente monoespaciada)

{_texto_paginas(paginas)}

## XHTML a corregir
{xhtml}
"""
    contenido: list[dict] = []
    if pdf_bytes:
        contenido.append(bloque_pdf(pdf_bytes))
    contenido.append({"type": "text", "text": texto})
    return limpiar_salida(claude.pedir(SISTEMA_REVISOR, contenido))
