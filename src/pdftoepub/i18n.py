"""User-interface messages in English and Spanish.

The language is chosen with `--lang`, the `PDFTOEPUB_LANG` environment variable,
or the system locale (`LANG`, `LC_ALL`, …), in that order. Defaults to English.
"""

from __future__ import annotations

import os

LANGUAGES = ("en", "es")

MESSAGES: dict[str, dict[str, str]] = {
    # --- CLI help ---------------------------------------------------------
    "cli.description": {
        "en": "Convert a PDF book into a clean, faithful EPUB using AI agents (Claude).",
        "es": "Convierte un libro PDF en un EPUB limpio y fiel usando agentes de IA (Claude).",
    },
    "cli.pdf": {"en": "input PDF book (or EPUB to translate)", "es": "libro PDF de entrada (o EPUB a traducir)"},
    "cli.output": {"en": "output EPUB (default: next to the PDF)", "es": "EPUB de salida (por defecto, junto al PDF)"},
    "cli.engine": {
        "en": "claude-code: use your local Claude Code (default); api: use ANTHROPIC_API_KEY",
        "es": "claude-code: usa tu Claude Code local (por defecto); api: usa ANTHROPIC_API_KEY",
    },
    "cli.model": {
        "en": "Claude model, e.g. claude-sonnet-5-5 or claude-opus-5-5 (default: the one recommended by the analysis)",
        "es": "modelo de Claude, p. ej. claude-sonnet-5-5 o claude-opus-5-5 (por defecto: el que recomiende el análisis)",
    },
    "cli.effort": {
        "en": "reasoning depth of the agents (default high)",
        "es": "profundidad de razonamiento de los agentes (por defecto high)",
    },
    "cli.pages_per_chunk": {
        "en": "pages the Layout agent processes per call",
        "es": "páginas que procesa el agente Maquetador en cada llamada",
    },
    "cli.parallel": {"en": "chapters converted in parallel", "es": "capítulos convertidos en paralelo"},
    "cli.reviews": {"en": "maximum Reviewer rounds per chunk", "es": "rondas máximas del agente Revisor por bloque"},
    "cli.pages": {"en": "convert only a page range, e.g. 1-40", "es": "convertir solo un rango de páginas, p. ej. 1-40"},
    "cli.no_vision": {
        "en": "do not send page images to the model (cheaper, worse with tables and scans)",
        "es": "no enviar la imagen de las páginas al modelo (más barato, peor con tablas y escaneos)",
    },
    "cli.no_highlight": {"en": "do not syntax-highlight code", "es": "no colorear la sintaxis del código"},
    "cli.math": {
        "en": "how to output formulas: mathml (sharp, scalable; default) or image (for readers without MathML, e.g. Kindle)",
        "es": "cómo generar las fórmulas: mathml (nítidas y escalables; por defecto) o image (para lectores sin MathML, p. ej. Kindle)",
    },
    "cli.diagrams": {
        "en": "format of vector diagrams: svg (sharp at any size; default) or png",
        "es": "formato de los diagramas vectoriales: svg (nítidos a cualquier tamaño; por defecto) o png",
    },
    "cli.no_ai": {"en": "basic conversion without AI (for testing)", "es": "conversión básica sin IA (para probar)"},
    "cli.estimate": {"en": "only estimate token usage and exit", "es": "solo estimar el consumo de tokens y salir"},
    "cli.lang": {"en": "interface language (en, es)", "es": "idioma de la interfaz (en, es)"},
    "cli.file_missing": {"en": "file not found: {path}", "es": "no existe el archivo {path}"},
    # --- Run --------------------------------------------------------------
    "run.interrupted": {
        "en": "\nInterrupted. Run the same command again to resume.",
        "es": "\nInterrumpido. Vuelve a lanzar el mismo comando para reanudar.",
    },
    "run.saved": {
        "en": "{error}\nCompleted work is saved: run the same command again to resume.",
        "es": "{error}\nEl trabajo hecho está guardado: relanza el mismo comando para reanudar.",
    },
    "run.extracting": {
        "en": "· Extracting text, code and images from {name}…",
        "es": "· Extrayendo texto, código e imágenes de {name}…",
    },
    "run.extracted": {
        "en": "  {pages} pages, {size}pt body text, {images} images/diagrams",
        "es": "  {pages} páginas, cuerpo de {size}pt, {images} imágenes/diagramas",
    },
    "run.scanned": {
        "en": ", {n} pages without text (will be read with vision)",
        "es": ", {n} páginas sin texto (se leerán con visión)",
    },
    "run.columns": {
        "en": "  {n} multi-column pages detected (text reordered into reading order)",
        "es": "  {n} páginas a varias columnas detectadas (texto reordenado en orden de lectura)",
    },
    "run.scanned_no_vision": {
        "en": "  ! There are scanned pages and vision is disabled: those pages will be empty.",
        "es": "  ! Hay páginas escaneadas y la visión está desactivada: esas páginas quedarán vacías.",
    },
    "run.cached_profile": {"en": "· Using the saved analysis in {path}", "es": "· Usando el análisis guardado en {path}"},
    "run.analyst": {
        "en": "· Analyst agent ({engine}): studying the book structure…",
        "es": "· Agente Analista ({engine}): estudiando la estructura del libro…",
    },
    "run.book": {"en": "· «{title}» — {n} chapters ({subject})", "es": "· «{title}» — {n} capítulos ({subject})"},
    "run.no_subject": {"en": "subject not detected", "es": "temática no detectada"},
    "run.layout": {"en": "· Layout agent converting chapters…", "es": "· Agente Maquetador convirtiendo capítulos…"},
    "run.chapter_done": {"en": "  ✓ Chapter {i}: {title}", "es": "  ✓ Capítulo {i}: {title}"},
    "run.page_too_big": {
        "en": "  ! Page {page} is too large to send as PDF; using text only.",
        "es": "  ! Página {page} demasiado pesada para enviarla como PDF; se usa solo el texto.",
    },
    "run.warnings_left": {
        "en": "  ! Pages {a}-{b}: warnings remain after {n} reviews: {problem}",
        "es": "  ! Págs. {a}-{b}: quedan avisos tras {n} revisiones: {problem}",
    },
    "run.reviewing": {
        "en": "  · Reviewer agent fixing pages {a}-{b}: {problem}",
        "es": "  · Agente Revisor corrigiendo págs. {a}-{b}: {problem}",
    },
    "run.split": {
        "en": "  · Pages {a}-{b}: output too long, splitting the chunk.",
        "es": "  · Págs. {a}-{b}: salida demasiado larga, se divide el bloque.",
    },
    "run.refused": {
        "en": "  ! Pages {a}-{b}: the model refused the content ({error}); using basic conversion.",
        "es": "  ! Págs. {a}-{b}: el modelo rechazó el contenido ({error}); se usa conversión básica.",
    },
    "run.basic_notice": {
        "en": "[Pages {a}–{b}: basic conversion without AI]",
        "es": "[Páginas {a}–{b}: conversión básica sin IA]",
    },
    "run.progress": {
        "en": "  [{done}/{total} chunks · {percent}%{eta}] {label}",
        "es": "  [{done}/{total} bloques · {percent}%{eta}] {label}",
    },
    "run.pages_label": {"en": "pages {a}-{b}", "es": "págs. {a}-{b}"},
    "run.eta": {"en": " · ~{time} left", "es": " · quedan ~{time}"},
    "run.resuming": {
        "en": "  Resuming: {done} of {total} chunks were already done.",
        "es": "  Reanudando: {done} de {total} bloques ya estaban hechos.",
    },
    "status.none": {
        "en": "No conversion in progress in {path}.",
        "es": "No hay ninguna conversión en curso en {path}.",
    },
    "status.line": {
        "en": "{done}/{total} chunks · {percent}%{eta} · last update {ago} ago",
        "es": "{done}/{total} bloques · {percent}%{eta} · última actualización hace {ago}",
    },
    "status.finished": {
        "en": "Conversion finished ({total} chunks).",
        "es": "Conversión terminada ({total} bloques).",
    },
    "cli.status": {
        "en": "show the progress of a conversion (running or interrupted) and exit",
        "es": "mostrar el progreso de una conversión (en curso o interrumpida) y salir",
    },
    # --- Pre-conversion analysis ------------------------------------------
    "an.header": {
        "en": "· Analysis of {name} ({sampled} of {pages} pages sampled):",
        "es": "· Análisis de {name} ({sampled} de {pages} páginas muestreadas):",
    },
    "an.f_scanned": {"en": "Scanned pages", "es": "Páginas escaneadas"},
    "an.f_math": {"en": "Pages with formulas", "es": "Páginas con fórmulas"},
    "an.f_columns": {"en": "Multi-column pages", "es": "Páginas a varias columnas"},
    "an.f_tables": {"en": "Pages with tables", "es": "Páginas con tablas"},
    "an.f_frames": {"en": "Pages with boxes/frames", "es": "Páginas con recuadros"},
    "an.f_code": {"en": "Pages with code", "es": "Páginas con código"},
    "an.f_images": {"en": "Pages with images", "es": "Páginas con imágenes"},
    "an.f_garbled": {"en": "Pages with unreadable text", "es": "Páginas con texto ilegible"},
    "an.recommend": {
        "en": "→ Recommended model: {model} — {label}",
        "es": "→ Modelo recomendado: {model} — {label}",
    },
    "an.sonnet": {
        "en": "practically the same result for this kind of PDF, at about half the usage",
        "es": "prácticamente el mismo resultado para este tipo de PDF, con la mitad de consumo",
    },
    "an.opus": {
        "en": "the best quality for difficult PDFs (uses about twice as much)",
        "es": "la máxima calidad para PDF difíciles (consume aproximadamente el doble)",
    },
    "an.reason_scanned": {
        "en": "{pct}% of the pages are scanned: they must be transcribed from the image",
        "es": "el {pct}% de las páginas están escaneadas: hay que transcribirlas desde la imagen",
    },
    "an.reason_math": {
        "en": "{pct}% of the pages have formulas, which must be read from the image into MathML",
        "es": "el {pct}% de las páginas tienen fórmulas, que hay que leer de la imagen y pasar a MathML",
    },
    "an.reason_columns": {
        "en": "{pct}% of the pages have several columns",
        "es": "el {pct}% de las páginas tienen varias columnas",
    },
    "an.reason_tables": {
        "en": "{pct}% of the pages have tables",
        "es": "el {pct}% de las páginas tienen tablas",
    },
    "an.reason_garbled": {
        "en": "{pct}% of the pages have unreadable extracted text",
        "es": "el {pct}% de las páginas tienen texto extraído ilegible",
    },
    "an.reason_simple": {
        "en": "clean text and a simple layout ({code}% of the pages with code)",
        "es": "texto limpio y maquetación sencilla ({code}% de las páginas con código)",
    },
    "an.tip_no_vision": {
        "en": "  Tip: clean text without tables, images or formulas: --no-vision would cut usage further.",
        "es": "  Consejo: texto limpio sin tablas, imágenes ni fórmulas: --no-vision reduciría aún más el consumo.",
    },
    "an.tip_math_kindle": {
        "en": "  Tip: if you read on Kindle, add --math image.",
        "es": "  Consejo: si lees en Kindle, añade --math image.",
    },
    "an.tip_scanned": {
        "en": "  Tip: don't use --no-vision: scanned pages are read from the image.",
        "es": "  Consejo: no uses --no-vision: las páginas escaneadas se leen desde la imagen.",
    },
    "an.ask": {
        "en": "Use {model}? [Y/n] (n = use {other}) ",
        "es": "¿Usar {model}? [S/n] (n = usar {other}) ",
    },
    "an.model_recommended": {"en": "· Model: {model} (recommended)", "es": "· Modelo: {model} (recomendado)"},
    "an.model_chosen": {"en": "· Model: {model} (your choice)", "es": "· Modelo: {model} (elegido por ti)"},
    "an.model_resumed": {
        "en": "· Model: {model} (the one this conversion started with; use --model to change it)",
        "es": "· Modelo: {model} (con el que empezó esta conversión; usa --model para cambiarlo)",
    },
    "an.model_differs": {
        "en": "  Note: the analysis recommends {model} for this PDF.",
        "es": "  Nota: el análisis recomienda {model} para este PDF.",
    },
    "cli.analyze": {
        "en": "analyze the PDF, recommend a model and exit (no AI, no usage)",
        "es": "analizar el PDF, recomendar un modelo y salir (sin IA, no consume)",
    },
    "cli.yes": {
        "en": "don't ask: use the recommended model directly",
        "es": "no preguntar: usar directamente el modelo recomendado",
    },
    # --- Translation --------------------------------------------------------
    "tr.glossary": {
        "en": "· Glossary agent ({engine}): preparing terminology and title…",
        "es": "· Agente Glosario ({engine}): preparando terminología y título…",
    },
    "tr.start": {
        "en": "· Translator agent: {name} → {target} ({docs} documents, {chunks} chunks)…",
        "es": "· Agente Traductor: {name} → {target} ({docs} documentos, {chunks} bloques)…",
    },
    "tr.label": {"en": "{doc} part {n}", "es": "{doc} parte {n}"},
    "tr.retry": {
        "en": "  · Translator agent fixing {doc} part {n}: {problem}",
        "es": "  · Agente Traductor corrigiendo {doc} parte {n}: {problem}",
    },
    "tr.kept": {
        "en": "  ! {doc} part {n}: could not be translated reliably ({problem}); original text kept.",
        "es": "  ! {doc} parte {n}: no se pudo traducir con fiabilidad ({problem}); se mantiene el texto original.",
    },
    "tr.labels": {
        "en": "· Translating the title and table of contents…",
        "es": "· Traduciendo el título y el índice…",
    },
    "tr.done": {"en": "✔ Translated EPUB: {path}", "es": "✔ EPUB traducido: {path}"},
    "tr.estimate": {
        "en": "{docs} documents · ~{input:.2f} M input tokens · ~{output:.2f} M output tokens (excluding retries)",
        "es": "{docs} documentos · ~{input:.2f} M tokens de entrada · ~{output:.2f} M de salida (sin contar reintentos)",
    },
    "tr.model": {
        "en": "· Model: {model} (recommended for translation; use --model to change it)",
        "es": "· Modelo: {model} (recomendado para traducir; usa --model para cambiarlo)",
    },
    "tr.need_ai": {
        "en": "Translation needs AI: remove --no-ai.",
        "es": "La traducción necesita IA: quita --no-ai.",
    },
    "tr.need_target": {
        "en": "To process an EPUB, say which language to translate it into, e.g. --translate es",
        "es": "Para procesar un EPUB indica a qué idioma traducirlo, p. ej. --translate es",
    },
    "cli.translate": {
        "en": "translate the EPUB into this language (e.g. es, en, fr, de, pt). The input can be a PDF "
              "(converted first) or an EPUB",
        "es": "traducir el EPUB a este idioma (p. ej. es, en, fr, de, pt). La entrada puede ser un PDF "
              "(se convierte primero) o un EPUB",
    },
    "run.assembling": {"en": "· Assembling EPUB…", "es": "· Ensamblando EPUB…"},
    "run.done": {"en": "✔ EPUB created: {path}", "es": "✔ EPUB generado: {path}"},
    "run.usage": {
        "en": "  {calls} calls to {engine} · {input:,} input tokens · {cached:,} cached · {output:,} output{cost}",
        "es": "  {calls} llamadas a {engine} · {input:,} tokens de entrada · {cached:,} de caché · {output:,} de salida{cost}",
    },
    "run.cost_plan": {
        "en": " · equivalent to ≈ {cost:.2f} USD of API usage (on a Pro/Max plan it comes out of your plan's usage, not billed)",
        "es": " · equivale a ≈ {cost:.2f} USD de API (con un plan Pro/Max sale de la cuota de tu plan, no se cobra)",
    },
    "run.cost_api": {"en": " · ≈ {cost:.2f} USD", "es": " · ≈ {cost:.2f} USD"},
    "run.estimate": {
        "en": "{pages} pages · ~{input:.2f} M input tokens · ~{output:.2f} M output tokens (excluding reviews)",
        "es": "{pages} páginas · ~{input:.2f} M tokens de entrada · ~{output:.2f} M de salida (sin contar revisiones)",
    },
    "run.price": {
        "en": "{model} API price: {pin} / {pout} USD per M tokens (input/output)",
        "es": "Precio de API de {model}: {pin} / {pout} USD por M de tokens (entrada/salida)",
    },
    # --- Errors -----------------------------------------------------------
    "err.password": {"en": "The PDF is password-protected.", "es": "El PDF está protegido con contraseña."},
    "err.no_claude": {
        "en": "The `claude` command was not found. Install Claude Code "
              "(https://claude.com/claude-code) and log in by running `claude` once.",
        "es": "No se encuentra el comando `claude`. Instala Claude Code "
              "(https://claude.com/claude-code) e inicia sesión ejecutando `claude` una vez.",
    },
    "err.timeout": {
        "en": "Claude Code did not respond within {minutes} minutes.",
        "es": "Claude Code no respondió en {minutes} minutos.",
    },
    "err.no_result": {
        "en": "Claude Code exited without a response (code {code}): {detail}",
        "es": "Claude Code terminó sin respuesta (código {code}): {detail}",
    },
    "err.claude_code": {"en": "Claude Code returned an error: {detail}", "es": "Claude Code devolvió un error: {detail}"},
    "err.no_structured": {
        "en": "Claude Code did not return the expected structured output.",
        "es": "Claude Code no devolvió la salida estructurada esperada.",
    },
    "err.refused": {
        "en": "The model refused to process this fragment.",
        "es": "El modelo rechazó procesar este fragmento.",
    },
    "err.no_api_key": {
        "en": "No API credentials. Set ANTHROPIC_API_KEY.",
        "es": "No hay credenciales de la API. Define ANTHROPIC_API_KEY.",
    },
    "err.bad_api_key": {
        "en": "Invalid API key. Check ANTHROPIC_API_KEY.",
        "es": "Clave de API no válida. Revisa ANTHROPIC_API_KEY.",
    },
    "err.api": {"en": "Anthropic API error: {detail}", "es": "Error de la API de Anthropic: {detail}"},
}

_current = "en"


def detect(argv: list[str] | None = None) -> str:
    """Language from `--lang`, then PDFTOEPUB_LANG, then the system locale."""
    argv = argv or []
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return _normalize(argv[i + 1])
        if arg.startswith("--lang="):
            return _normalize(arg.split("=", 1)[1])
    for var in ("PDFTOEPUB_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        if value := os.environ.get(var):
            return _normalize(value)
    return "en"


def _normalize(value: str) -> str:
    code = value.lower()[:2]
    return code if code in LANGUAGES else "en"


def set_language(lang: str) -> None:
    global _current
    _current = _normalize(lang)


def language() -> str:
    return _current


def t(key: str, **kwargs) -> str:
    text = MESSAGES[key].get(_current) or MESSAGES[key]["en"]
    return text.format(**kwargs) if kwargs else text
