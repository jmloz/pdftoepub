# pdftoepub

[English](README.md) · **Español**

**Convierte libros PDF en EPUB limpios, coherentes y fieles al original usando agentes de IA (Claude).**

Pensado especialmente para libros técnicos: programación, Linux, administración de sistemas,
redes, comandos… El código se conserva carácter por carácter, en bloques legibles y con
resaltado de sintaxis, y el texto se reflota para leerse cómodamente en cualquier lector
de libros electrónicos.

> [!IMPORTANT]
> **Necesitas Claude Code instalado y con la sesión iniciada en tu equipo.**
> Los agentes de IA se ejecutan a través del comando `claude` local (modo no interactivo
> `claude -p`), así que el programa usa tu cuenta de Claude Code (suscripción Pro / Max o
> cuenta de Claude Console). Ver [Requisitos](#requisitos).

---

## Índice

- [Por qué otro conversor](#por-qué-otro-conversor)
- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Uso rápido](#uso-rápido)
- [Opciones](#opciones)
- [Idioma de la interfaz](#idioma-de-la-interfaz)
- [Cómo funciona](#cómo-funciona)
- [Ejemplo de resultado](#ejemplo-de-resultado)
- [Reanudar y corregir una conversión](#reanudar-y-corregir-una-conversión)
- [Elección del modelo](#elección-del-modelo)
- [Consumo y costes](#consumo-y-costes)
- [Motor alternativo: API de Anthropic](#motor-alternativo-api-de-anthropic)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Desarrollo y pruebas](#desarrollo-y-pruebas)
- [Matemáticas, diagramas y maquetaciones complejas](#matemáticas-diagramas-y-maquetaciones-complejas)
- [Limitaciones](#limitaciones)
- [Solución de problemas](#solución-de-problemas)
- [Privacidad y derechos de autor](#privacidad-y-derechos-de-autor)
- [Licencia](#licencia)

---

## Por qué otro conversor

Los conversores clásicos (Calibre, pandoc, etc.) tratan el PDF como un dibujo de letras en
posiciones fijas. Con libros técnicos el resultado suele ser malo:

- Los bloques de código se convierten en párrafos, pierden la sangría o se mezclan con el texto.
- Las palabras cortadas con guion a final de línea quedan partidas (`termi- nal`).
- Cabeceras, pies y números de página aparecen en mitad del texto.
- Las tablas se deshacen y los comandos largos se cortan.

`pdftoepub` combina una extracción precisa del PDF con agentes de IA que **entienden** lo
que están leyendo (esto es un título, esto es una sesión de terminal, esto es una tabla de
opciones) y comprobaciones automáticas que garantizan que no se pierde contenido.

## Características

- 🧠 **Tres agentes especializados**: Analista (estructura), Maquetador (conversión) y Revisor (corrección).
- 💻 **Código fiel al original**: monoespaciada detectada en el PDF, sangría reconstruida, comillas rectas,
  `--opciones` intactas, lenguaje identificado (`bash`, `console`, `python`, `c`, `yaml`…) y resaltado de sintaxis.
- 👁️ **Visión**: el modelo ve la página real además del texto, así reconstruye tablas, recuadros de notas
  y figuras correctamente. Los PDF escaneados se transcriben desde la imagen.
- ✅ **Verificación automática**: cada bloque se comprueba (cobertura de palabras, líneas de código,
  XHTML válido); si falta algo, el agente Revisor lo corrige.
- ➗ **Matemáticas**: las fórmulas se convierten en MathML (nítidas y escalables) o, para lectores
  sin MathML como Kindle, en imágenes con su LaTeX como texto alternativo.
- 📐 **Diagramas vectoriales en SVG**: nítidos con cualquier zoom, con PNG como alternativa.
- 📰 **Maquetaciones complejas**: las páginas a varias columnas se leen en el orden correcto, y los
  recuadros, notas y advertencias enmarcados se mantienen juntos.
- 🔗 **Continuidad entre páginas**: párrafos, listas, tablas y bloques de código partidos entre páginas se unen.
- 📚 **EPUB 3 completo**: portada, metadatos, índice navegable con capítulos y secciones, imágenes y diagramas,
  hoja de estilos pensada para lectores (el código largo se ajusta en vez de cortarse).
- ♻️ **Reanudable**: todo se guarda en una carpeta de trabajo; si se interrumpe, continúa donde lo dejó.
- 🌍 **Interfaz en inglés o español**; el libro siempre conserva su idioma original.
- ⚙️ **Ajustable**: modelo, nivel de esfuerzo, tamaño de bloque, paralelismo, rango de páginas…

## Requisitos

| Requisito | Detalles |
|---|---|
| **Claude Code** | Instalado y con sesión iniciada. Debe funcionar el comando `claude` en tu terminal. Necesita una suscripción Claude Pro/Max o una cuenta de Claude Console. [Instalar Claude Code](https://claude.com/claude-code) |
| **Python 3.13+** | |
| **[uv](https://docs.astral.sh/uv/)** | Gestor de proyectos de Python (recomendado). |
| Sistema | Linux, macOS o Windows (WSL). |

Comprueba que Claude Code está listo:

```bash
claude --version          # debe mostrar la versión
claude -p "di hola"       # debe responder sin pedir login
```

Si es la primera vez, ejecuta `claude` una vez para iniciar sesión.

## Instalación

```bash
git clone https://github.com/jmloz/pdftoepub.git
cd pdftoepub
uv sync
```

Opcionalmente, instálalo como comando global:

```bash
uv tool install .
pdftoepub --help
```

## Uso rápido

```bash
# 1. Analiza el PDF: modelo recomendado y consumo estimado (gratis, sin IA)
uv run pdftoepub libro.pdf --analyze

# 2. Prueba con las primeras páginas y revisa el resultado
uv run pdftoepub libro.pdf --pages 1-30 -o prueba.epub

# 3. Conversión completa (genera libro.epub junto al PDF)
uv run pdftoepub libro.pdf
```

Durante la conversión verás el progreso de cada agente:

```
· Extrayendo texto, código e imágenes de libro.pdf…
  412 páginas, cuerpo de 10pt, 57 imágenes/diagramas
· Agente Analista (Claude Code): estudiando la estructura del libro…
· «Guía práctica de Linux» — 18 capítulos (Sistemas operativos / Linux)
· Agente Maquetador convirtiendo capítulos…
  · Agente Revisor corrigiendo págs. 37-42: Faltan o están alteradas 4 de 61 líneas de código…
  ✓ Capítulo 1: Introducción a la terminal
  ...
· Ensamblando EPUB…
✔ EPUB generado: libro.epub
```

Más ejemplos:

```bash
# Más rápido y con menos consumo: sin enviar imágenes de página
uv run pdftoepub libro.pdf --no-vision

# Máxima calidad para un libro difícil (tablas complejas, mucho código)
uv run pdftoepub libro.pdf --model claude-opus-5-5 --effort xhigh --pages-per-chunk 4

# Conversión básica sin IA (no necesita Claude Code; útil para probar)
uv run pdftoepub libro.pdf --no-ai
```

## Opciones

| Opción | Por defecto | Descripción |
|---|---|---|
| `-o, --output` | `<pdf>.epub` | Ruta del EPUB generado. |
| `--engine` | `claude-code` | `claude-code` usa tu Claude Code local; `api` usa la API de Anthropic ([ver abajo](#motor-alternativo-api-de-anthropic)). |
| `--model` | recomendado | Modelo de Claude que usan los agentes: `claude-sonnet-5-5` o `claude-opus-5-5`. Por defecto, el que recomiende el análisis ([ver abajo](#elección-del-modelo)). |
| `--effort` | `high` | Profundidad de razonamiento: `low`, `medium`, `high`, `xhigh`, `max`. |
| `--pages-per-chunk` | `6` | Páginas que procesa el Maquetador en cada llamada. Menos páginas = más precisión y más llamadas. |
| `--parallel` | `3` | Capítulos que se convierten a la vez. |
| `--reviews` | `2` | Rondas máximas del agente Revisor por bloque. |
| `--pages` | todo | Convierte solo un rango de páginas, p. ej. `1-40`. |
| `--no-vision` | — | No envía la imagen de las páginas al modelo (menos consumo; peor con tablas, figuras y escaneos). |
| `--no-highlight` | — | Código sin colores de sintaxis. |
| `--math` | `mathml` | Fórmulas en `mathml` (nítidas, escalables y buscables) o `image` (para lectores sin MathML, p. ej. Kindle). |
| `--diagrams` | `svg` | Diagramas vectoriales en `svg` (nítidos a cualquier tamaño) o `png`. |
| `--no-ai` | — | Conversión básica determinista, sin agentes. |
| `--analyze` | — | Analiza el PDF, recomienda un modelo, estima el consumo y sale (sin IA, gratis). |
| `-y, --yes` | — | No preguntar: usa directamente el modelo recomendado. |
| `--estimate` | — | Solo estima los tokens que consumirá la conversión. |
| `--status` | — | Muestra el progreso de una conversión en curso o interrumpida y sale. |
| `--lang` | auto | Idioma de la interfaz: `en` o `es` ([ver abajo](#idioma-de-la-interfaz)). |

## Idioma de la interfaz

Los mensajes y la ayuda del programa están en **inglés** y **español**. El idioma se elige
en este orden:

1. La opción `--lang`: `pdftoepub libro.pdf --lang es`
2. La variable de entorno `PDFTOEPUB_LANG`: `export PDFTOEPUB_LANG=es`
3. El idioma de tu sistema (`LANG`, `LC_ALL`…): si empieza por `es`, se usa español.
4. Si no, inglés.

Para tenerlo siempre en español, añade `export PDFTOEPUB_LANG=es` a tu `~/.bashrc` o `~/.zshrc`.

El idioma de la interfaz **no** afecta al libro: el EPUB siempre conserva el idioma del PDF original.

## Cómo funciona

```
          ┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐
 PDF ───► │  Extractor   │──►│ Agente Analista  │──►│ Agente Maquetador  │──┐
          │   (sin IA)   │   │   (estructura)   │   │ (bloque a bloque)  │  │
          └──────────────┘   └──────────────────┘   └────────────────────┘  │
                                                              ▲             ▼
                                                    ┌─────────┴───┐  ┌─────────────┐
                                                    │   Agente    │◄─│ Verificador │
                                                    │   Revisor   │  │  (sin IA)   │
                                                    └─────────────┘  └──────┬──────┘
                                                                            ▼
                                                    ┌──────────────┐
                                           EPUB ◄───│ Ensamblador  │
                                                    │   (sin IA)   │
                                                    └──────────────┘
```

1. **Extractor** (`extractor.py`, sin IA). Lee el PDF con PyMuPDF y genera un texto *anotado*:
   el texto sale en **orden de lectura**:
   - detecta las páginas a varias columnas: los elementos a todo el ancho (títulos, figuras)
     dividen la página en franjas, y dentro de cada franja las columnas que están una al lado
     de otra se leen de izquierda a derecha;
   - el texto dentro de un marco dibujado se mantiene junto y se marca como recuadro (nota,
     advertencia…);
   - las líneas en fuente monoespaciada se marcan como código y se reconstruye su sangría a
     partir de la posición horizontal (por columna);
   - el texto en fuente matemática (Computer Modern, STIX, Cambria Math, Symbol…) se marca
     como fórmula;
   - las líneas con letra más grande que el cuerpo se marcan como posibles títulos;
   - se eliminan cabeceras, pies y números de página que se repiten;
   - se exportan las imágenes, y los diagramas vectoriales (uniendo los trazos cercanos en
     una sola figura) se exportan como SVG, o como PNG si el SVG pesara demasiado.

2. **Agente Analista**. Estudia los marcadores del PDF, los posibles títulos y las primeras
   páginas, y produce el *perfil* del libro: título, autores, idioma, temática, lenguajes de
   código presentes, convenciones tipográficas y la división en capítulos. Se guarda en
   `profile.json`.

3. **Agente Maquetador**. Para cada bloque de páginas recibe la imagen de esas páginas (PDF),
   el texto anotado, el perfil del libro y el final del bloque anterior. Devuelve XHTML
   semántico: párrafos completos, palabras cortadas reparadas, `<pre><code class="language-…">`
   para código, tablas, listas, notas, teclas, figuras con texto alternativo, notas al pie y
   fórmulas en MathML (leyéndolas de la imagen de la página, porque las fórmulas extraídas de
   un PDF suelen salir corruptas).
   Su regla principal es la **fidelidad**: reestructura, nunca resume ni reescribe.

4. **Verificador** (`verifier.py`, sin IA). Comprueba cada bloque:
   - el XHTML está bien formado;
   - aparece al menos el 93 % de las palabras del original;
   - aparecen todas las líneas de código del original;
   - las imágenes referenciadas existen.

5. **Agente Revisor**. Solo interviene si el verificador detecta problemas: recibe la lista
   concreta de fallos y devuelve el bloque corregido. Se repite hasta `--reviews` veces.

6. **Ensamblador** (`assembler.py`, sin IA). Une los bloques de cada capítulo (incluidos
   párrafos y código partidos entre páginas), colorea el código con Pygments, numera las
   secciones, genera el índice navegable y la portada, y escribe el EPUB 3.

## Ejemplo de resultado

Página de un PDF con un párrafo cortado entre líneas, un comando y un script:

```text
Este capítulo explica cómo usar la termi-
nal de Linux para gestionar archivos de manera eficiente.
1.1 Listar archivos
El comando ls muestra el contenido de un directorio:
$ ls -la /etc | grep --color=auto conf
for f in *.txt; do
    echo "$f" >> lista.log
done
```

XHTML generado por los agentes:

```html
<h1>Capítulo 1. Comandos básicos</h1>
<p>Este capítulo explica cómo usar la terminal de Linux para gestionar archivos de manera eficiente.</p>
<h2>1.1 Listar archivos</h2>
<p>El comando <code>ls</code> muestra el contenido de un directorio:</p>
<pre><code class="language-console">$ ls -la /etc | grep --color=auto conf</code></pre>
<pre><code class="language-bash">for f in *.txt; do
    echo "$f" &gt;&gt; lista.log
done</code></pre>
```

La palabra partida se une, el comando de terminal (`console`) y el script (`bash`) se
separan e identifican, y la sangría del código se conserva.

## Reanudar y corregir una conversión

Durante la conversión, cada bloque terminado muestra el progreso total y una estimación
del tiempo restante:

```
  [45/254 bloques · 17% · quedan ~1h 12m] págs. 101-106
```

Desde otra terminal (o tras una interrupción) puedes consultarlo cuando quieras:

```bash
uv run pdftoepub libro.pdf --status
# 45/254 bloques · 17% · quedan ~1h 12m · última actualización hace 1m
```

Todo el trabajo intermedio se guarda en una carpeta junto al EPUB (`libro.work/`):

```
libro.work/
├── profile.json     # análisis del libro (metadatos y capítulos)
├── progress.json    # bloques hechos / total, lo usa --status
├── settings.json    # modelo con el que empezó la conversión (se mantiene al reanudar)
├── chunks/         # XHTML de cada bloque ya convertido
├── images/         # imágenes y diagramas extraídos
└── cover.png
```

- **Si se interrumpe** (Ctrl+C, corte de red, límite de uso alcanzado), vuelve a lanzar el
  mismo comando: solo se procesan los bloques que faltan.
- **Si un capítulo está mal cortado** o el título/autor no es correcto, edita `profile.json`,
  borra la carpeta `chunks/` y vuelve a ejecutar. El análisis no se repite.
- **Para regenerar un bloque concreto**, borra su archivo en `chunks/` y vuelve a ejecutar.
- **Para empezar de cero**, borra la carpeta `libro.work/`.

## Elección del modelo

Antes de convertir, `pdftoepub` analiza una muestra de hasta 80 páginas del PDF (en menos de
un segundo y sin IA) y recomienda el modelo adecuado:

| El PDF tiene… | Recomendación |
|---|---|
| Texto limpio y maquetación sencilla (la mayoría de libros de programación, Linux y comandos) | **Sonnet** (`claude-sonnet-5-5`): prácticamente el mismo resultado con la mitad de consumo |
| ≥10 % de páginas escaneadas, ≥15 % con fórmulas, ≥20 % a varias columnas, ≥25 % con tablas o ≥5 % con texto ilegible | **Opus** (`claude-opus-5-5`): la máxima calidad para PDF difíciles |

```
· Análisis de JavaNotesForProfessionals.pdf (80 de 982 páginas muestreadas):
  Páginas escaneadas                   1%
  Páginas con fórmulas                 0%
  Páginas a varias columnas            2%
  Páginas con tablas                   0%
  Páginas con recuadros               82%
  Páginas con código                  90%
  Páginas con imágenes                 5%
  Páginas con texto ilegible           0%
→ Modelo recomendado: claude-sonnet-5-5 — prácticamente el mismo resultado para este tipo de PDF, con la mitad de consumo
   · texto limpio y maquetación sencilla (90% de las páginas con código)
¿Usar claude-sonnet-5-5? [S/n] (n = usar claude-opus-5-5)
```

- **En una terminal** te pide confirmación: pulsa Enter para aceptar o `n` para usar el otro modelo.
- **Con `--yes`** (o si no se ejecuta desde una terminal, p. ej. en un script) usa directamente la recomendación.
- **Con `--model`** manda siempre tu elección; si el análisis recomienda otro modelo, te avisa.
- **Al reanudar** una conversión interrumpida, mantiene el modelo con el que empezó (guardado en
  `libro.work/settings.json`), para no convertir un libro con dos modelos sin darte cuenta. Usa
  `--model` para cambiarlo a propósito.
- `--analyze` muestra el informe y el consumo estimado sin convertir nada.

El análisis también da consejos, como `--math image` para Kindle cuando el libro tiene fórmulas,
o `--no-vision` cuando el texto es limpio y no tiene tablas, imágenes ni fórmulas.

## Consumo y costes

Con el motor por defecto (Claude Code) se paga como Claude Code esté configurado en tu equipo:

| Cómo iniciaste sesión en Claude Code | Qué consume `pdftoepub` |
|---|---|
| Suscripción **Claude Pro / Max** | La **cuota de uso de tu plan**. No se cobra dinero aparte (salvo que tengas activado el uso adicional de pago). |
| Cuenta de **Claude Console** (API) o `ANTHROPIC_API_KEY` en el entorno | **Dinero real**, por tokens, según los precios de la API. |

Al terminar, el programa muestra los tokens usados y su **equivalente** en precio de API.
Es solo una referencia: con una suscripción Pro/Max no se te cobra esa cantidad.

> [!WARNING]
> Si tienes la variable `ANTHROPIC_API_KEY` definida en tu terminal, Claude Code la usa
> en lugar de tu suscripción y se factura por tokens. Para usar tu plan, quítala antes de
> lanzar el programa: `unset ANTHROPIC_API_KEY`.

Orientativamente, cada página cuesta unos 2.000–4.000 tokens de entrada (texto + imagen de
la página) y una cantidad de salida similar al texto de la página. Un libro de 400 páginas
puede consumir una parte importante de la cuota de un plan Pro; en planes Max o con
`--no-vision` y `--effort medium` el consumo es mucho más llevadero. El modelo por defecto es
Sonnet; `--model claude-opus-5-5` consume aproximadamente el doble.

Consejos para ahorrar:

- Usa `--estimate` antes de empezar y `--pages` para probar con pocas páginas.
- `--no-vision` reduce mucho la entrada; úsalo si el PDF tiene texto limpio y pocas tablas.
- `--effort medium` para libros sencillos; reserva `--model claude-opus-5-5` para los más difíciles.
- Si alcanzas el límite de uso, espera a que se renueve y relanza: la conversión se reanuda.

## Motor alternativo: API de Anthropic

Si prefieres no usar Claude Code (por ejemplo, en un servidor), puedes usar la API de
Anthropic con una clave de [Claude Console](https://console.anthropic.com/). En ese caso
se factura por tokens.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pdftoepub libro.pdf --engine api
```

Con este motor, si el modelo rechaza un fragmento (puede ocurrir con libros de seguridad
informática), la API lo reintenta automáticamente con un modelo alternativo.

## Estructura del proyecto

```
pdftoepub/
├── src/pdftoepub/
│   ├── __init__.py     # interfaz de línea de comandos
│   ├── pipeline.py     # coordina el flujo, la caché y el paralelismo
│   ├── analysis.py     # análisis previo y recomendación del modelo
│   ├── extractor.py    # lectura del PDF (orden de lectura, texto anotado, imágenes, SVG, fórmulas)
│   ├── agents.py       # prompts y lógica de los agentes Analista, Maquetador y Revisor
│   ├── verifier.py     # comprobaciones automáticas de calidad
│   ├── assembler.py    # unión de bloques, resaltado y generación del EPUB
│   ├── llm.py          # motores de IA: Claude Code local y API de Anthropic
│   └── i18n.py         # mensajes de la interfaz en inglés y español
├── tests/
│   └── test_pipeline.py
├── pyproject.toml
├── README.md           # documentación en inglés
└── README.es.md        # documentación en español
```

El código, los comentarios y los prompts de los agentes están en inglés; los agentes
mantienen siempre el idioma del libro.

## Desarrollo y pruebas

```bash
uv sync
uv run pytest
```

Las pruebas usan un Claude simulado y un ejecutable `claude` falso, así que **no consumen
cuota** ni necesitan conexión. Cubren el flujo completo (análisis → maquetación → revisión →
EPUB), la caché/reanudación, la unión de bloques partidos entre páginas, el verificador, el
motor de Claude Code, los idiomas de la interfaz, el orden de lectura a varias columnas,
los recuadros, los diagramas SVG y el MathML.

Para hacer una prueba real rápida con Claude Code: `uv run pdftoepub libro.pdf --pages 1-5`.

Para añadir un mensaje nuevo a la interfaz, añade su clave con los textos `en` y `es` a
`MESSAGES` en `i18n.py` (un test comprueba que todos los mensajes tengan los dos idiomas).

## Matemáticas, diagramas y maquetaciones complejas

**Matemáticas.** Por defecto las fórmulas se escriben en MathML, el estándar de EPUB 3 para
matemáticas: se ven nítidas a cualquier tamaño, siguen la configuración de letra del lector y
guardan su LaTeX como `alttext` para los lectores de pantalla. Apple Books, Kobo, Thorium y la
mayoría de lectores modernos muestran MathML. **Kindle** y algunos lectores antiguos no: para
ellos usa `--math image`, que convierte las fórmulas destacadas en imágenes (SVG) con su LaTeX
como texto alternativo y escribe las fórmulas sencillas dentro del texto con cursiva,
subíndices y superíndices.

**Diagramas.** Los dibujos vectoriales del PDF (diagramas de flujo, de red, gráficas) se
exportan como SVG para que se vean nítidos al hacer zoom. Usa `--diagrams png` si tu lector
tiene problemas con imágenes SVG.

**Maquetaciones complejas.** Las páginas a varias columnas se reordenan en orden de lectura
antes de que las vean los agentes, y el Maquetador además comprueba el orden con la imagen de
la página. El texto dentro de marcos dibujados se mantiene junto y se convierte en recuadros
`<aside>` (o en listados de código / tablas cuando eso es lo que contiene el marco).

Cambiar `--math` regenera los bloques (forma parte de la clave de la caché); `--diagrams` solo
cambia las imágenes exportadas.

## Limitaciones

- Las fórmulas se detectan por la fuente: las escritas con una fuente de texto normal no se
  marcan como fórmula, aunque el Maquetador suele reconocerlas igualmente en la imagen de la página.
- **Maquetaciones muy inusuales** (revistas con texto que rodea imágenes, texto girado) pueden
  requerir revisar `profile.json` o reducir `--pages-per-chunk`.
- Los **números de página** del original desaparecen; las referencias tipo "véase la página 42"
  se conservan como texto.
- PDF protegidos con contraseña no están soportados.
- El libro se mantiene en su idioma original (no traduce).

## Solución de problemas

| Problema | Solución |
|---|---|
| `No se encuentra el comando claude` / `The claude command was not found` | Instala Claude Code y comprueba que `claude` está en tu `PATH`. |
| `Claude Code devolvió un error` / pide iniciar sesión | Ejecuta `claude` en una terminal e inicia sesión; prueba `claude -p "hola"`. |
| Se para por límite de uso | Espera a que se renueve la cuota y relanza el mismo comando: se reanuda. |
| Capítulos mal divididos | Edita `libro.work/profile.json`, borra `chunks/` y relanza. |
| Un bloque se ve mal | Borra su archivo en `libro.work/chunks/` y relanza, quizá con `--pages-per-chunk 3`. |
| `quedan avisos tras N revisiones` | El bloque se incluye igualmente; revísalo en el EPUB o regenéralo con más `--reviews`. |
| Páginas escaneadas vacías | No uses `--no-vision`: esas páginas se leen desde la imagen. |
| Las fórmulas no se ven en mi lector (p. ej. Kindle) | Vuelve a convertir con `--math image`. |
| Los diagramas no se ven | Vuelve a convertir con `--diagrams png`. |

## Privacidad y derechos de autor

- El contenido del PDF (texto e imágenes de las páginas) se envía a Anthropic para que
  Claude lo procese, igual que cualquier conversación con Claude Code. No conviertas
  documentos que no puedas compartir con un servicio externo.
- Usa esta herramienta solo con libros que tengas derecho a convertir (por ejemplo, para
  leer en tu propio lector un libro que has comprado). No distribuyas los EPUB generados
  de obras protegidas.

## Licencia

Distribuido bajo la licencia [MIT](LICENSE).
