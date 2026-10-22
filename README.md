# pdftoepub

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
- [Cómo funciona](#cómo-funciona)
- [Ejemplo de resultado](#ejemplo-de-resultado)
- [Reanudar y corregir una conversión](#reanudar-y-corregir-una-conversión)
- [Consumo y costes](#consumo-y-costes)
- [Motor alternativo: API de Anthropic](#motor-alternativo-api-de-anthropic)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Desarrollo y pruebas](#desarrollo-y-pruebas)
- [Limitaciones](#limitaciones)
- [Solución de problemas](#solución-de-problemas)
- [Privacidad y derechos de autor](#privacidad-y-derechos-de-autor)

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
- 🔗 **Continuidad entre páginas**: párrafos, listas, tablas y bloques de código partidos entre páginas se unen.
- 📚 **EPUB 3 completo**: portada, metadatos, índice navegable con capítulos y secciones, imágenes y diagramas,
  hoja de estilos pensada para lectores (el código largo se ajusta en vez de cortarse).
- ♻️ **Reanudable**: todo se guarda en una carpeta de trabajo; si se interrumpe, continúa donde lo dejó.
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
# 1. Mira cuánto va a consumir
uv run pdftoepub libro.pdf --estimar

# 2. Prueba con las primeras páginas y revisa el resultado
uv run pdftoepub libro.pdf --rango 1-30 -o prueba.epub

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
# Más rápido y con menos consumo: modelo Sonnet y sin enviar imágenes de página
uv run pdftoepub libro.pdf --modelo claude-sonnet-5-5 --sin-vision

# Máxima calidad para un libro difícil (tablas complejas, mucho código)
uv run pdftoepub libro.pdf --esfuerzo xhigh --paginas-por-bloque 4

# Conversión básica sin IA (no necesita Claude Code; útil para probar)
uv run pdftoepub libro.pdf --sin-ia
```

## Opciones

| Opción | Por defecto | Descripción |
|---|---|---|
| `-o, --salida` | `<pdf>.epub` | Ruta del EPUB generado. |
| `--motor` | `claude-code` | `claude-code` usa tu Claude Code local; `api` usa la API de Anthropic ([ver abajo](#motor-alternativo-api-de-anthropic)). |
| `--modelo` | `claude-opus-5-5` | Modelo de Claude que usan los agentes. |
| `--esfuerzo` | `high` | Profundidad de razonamiento: `low`, `medium`, `high`, `xhigh`, `max`. |
| `--paginas-por-bloque` | `6` | Páginas que procesa el Maquetador en cada llamada. Menos páginas = más precisión y más llamadas. |
| `--paralelo` | `3` | Capítulos que se convierten a la vez. |
| `--revisiones` | `2` | Rondas máximas del agente Revisor por bloque. |
| `--rango` | todo | Convierte solo un rango de páginas, p. ej. `1-40`. |
| `--sin-vision` | — | No envía la imagen de las páginas al modelo (menos consumo; peor con tablas, figuras y escaneos). |
| `--sin-resaltado` | — | Código sin colores de sintaxis. |
| `--sin-ia` | — | Conversión básica determinista, sin agentes. |
| `--estimar` | — | Solo estima los tokens que consumirá la conversión. |

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
   las líneas en fuente monoespaciada se marcan como código y se reconstruye su sangría a
   partir de la posición horizontal; las líneas con letra más grande que el cuerpo se marcan
   como posibles títulos; se eliminan cabeceras, pies y números de página que se repiten;
   se exportan las imágenes y se rasterizan los diagramas vectoriales.

2. **Agente Analista**. Estudia los marcadores del PDF, los posibles títulos y las primeras
   páginas, y produce el *perfil* del libro: título, autores, idioma, temática, lenguajes de
   código presentes, convenciones tipográficas y la división en capítulos. Se guarda en
   `perfil.json`.

3. **Agente Maquetador**. Para cada bloque de páginas recibe la imagen de esas páginas (PDF),
   el texto anotado, el perfil del libro y el final del bloque anterior. Devuelve XHTML
   semántico: párrafos completos, palabras cortadas reparadas, `<pre><code class="language-…">`
   para código, tablas, listas, notas, teclas, figuras con texto alternativo y notas al pie.
   Su regla principal es la **fidelidad**: reestructura, nunca resume ni reescribe.

4. **Verificador** (`verificador.py`, sin IA). Comprueba cada bloque:
   - el XHTML está bien formado;
   - aparece al menos el 93 % de las palabras del original;
   - aparecen todas las líneas de código del original;
   - las imágenes referenciadas existen.

5. **Agente Revisor**. Solo interviene si el verificador detecta problemas: recibe la lista
   concreta de fallos y devuelve el bloque corregido. Se repite hasta `--revisiones` veces.

6. **Ensamblador** (`ensamblador.py`, sin IA). Une los bloques de cada capítulo (incluidos
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

Todo el trabajo intermedio se guarda en una carpeta junto al EPUB (`libro.trabajo/`):

```
libro.trabajo/
├── perfil.json     # análisis del libro (metadatos y capítulos)
├── bloques/        # XHTML de cada bloque ya convertido
├── imagenes/       # imágenes y diagramas extraídos
└── portada.png
```

- **Si se interrumpe** (Ctrl+C, corte de red, límite de uso alcanzado), vuelve a lanzar el
  mismo comando: solo se procesan los bloques que faltan.
- **Si un capítulo está mal cortado** o el título/autor no es correcto, edita `perfil.json`,
  borra la carpeta `bloques/` y vuelve a ejecutar. El análisis no se repite.
- **Para regenerar un bloque concreto**, borra su archivo en `bloques/` y vuelve a ejecutar.
- **Para empezar de cero**, borra la carpeta `libro.trabajo/`.

## Consumo y costes

Con el motor por defecto (Claude Code) **no se cobra nada aparte**: el consumo se descuenta
de la cuota de uso de tu plan de Claude. Al terminar, el programa muestra los tokens usados
y su equivalente aproximado en precio de API como referencia.

Orientativamente, cada página cuesta unos 2.000–4.000 tokens de entrada (texto + imagen de
la página) y una cantidad de salida similar al texto de la página. Un libro de 400 páginas
puede consumir una parte importante de la cuota de un plan Pro; en planes Max o con
`--modelo claude-sonnet-5-5` y `--sin-vision` el consumo es mucho más llevadero.

Consejos para ahorrar:

- Usa `--estimar` antes de empezar y `--rango` para probar con pocas páginas.
- `--sin-vision` reduce mucho la entrada; úsalo si el PDF tiene texto limpio y pocas tablas.
- `--modelo claude-sonnet-5-5` y/o `--esfuerzo medium` para libros sencillos.
- Si alcanzas el límite de uso, espera a que se renueve y relanza: la conversión se reanuda.

## Motor alternativo: API de Anthropic

Si prefieres no usar Claude Code (por ejemplo, en un servidor), puedes usar la API de
Anthropic con una clave de [Claude Console](https://console.anthropic.com/). En ese caso
se factura por tokens.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pdftoepub libro.pdf --motor api
```

Con este motor, si el modelo rechaza un fragmento (puede ocurrir con libros de seguridad
informática), la API lo reintenta automáticamente con un modelo alternativo.

## Estructura del proyecto

```
pdftoepub/
├── src/pdftoepub/
│   ├── __init__.py       # interfaz de línea de comandos
│   ├── orquestador.py    # coordina el flujo, la caché y el paralelismo
│   ├── extractor.py      # lectura del PDF (texto anotado, imágenes, diagramas)
│   ├── agentes.py        # prompts y lógica de los agentes Analista, Maquetador y Revisor
│   ├── verificador.py    # comprobaciones automáticas de calidad
│   ├── ensamblador.py    # unión de bloques, resaltado y generación del EPUB
│   └── llm.py            # motores de IA: Claude Code local y API de Anthropic
├── tests/
│   └── test_conversion.py
├── pyproject.toml
└── README.md
```

## Desarrollo y pruebas

```bash
uv sync
uv run pytest
```

Las pruebas usan un Claude simulado y un ejecutable `claude` falso, así que **no consumen
cuota** ni necesitan conexión. Cubren el flujo completo (análisis → maquetación → revisión →
EPUB), la caché/reanudación, la unión de bloques partidos entre páginas, el verificador y el
motor de Claude Code.

Para hacer una prueba real rápida con Claude Code: `uv run pdftoepub libro.pdf --rango 1-5`.

## Limitaciones

- **Fórmulas matemáticas** complejas se transcriben como texto; no se genera MathML.
- **Diagramas vectoriales** se convierten en imágenes PNG (no se mantienen como vectores).
- **Maquetaciones muy complejas** (revistas, varias columnas con recuadros flotantes) pueden
  requerir revisar `perfil.json` o reducir `--paginas-por-bloque`.
- Los **números de página** del original desaparecen; las referencias tipo "véase la página 42"
  se conservan como texto.
- PDF protegidos con contraseña no están soportados.
- El libro se mantiene en su idioma original (no traduce).

## Solución de problemas

| Problema | Solución |
|---|---|
| `No se encuentra el comando claude` | Instala Claude Code y comprueba que `claude` está en tu `PATH`. |
| `Claude Code devolvió un error` / pide iniciar sesión | Ejecuta `claude` en una terminal e inicia sesión; prueba `claude -p "hola"`. |
| Se para por límite de uso | Espera a que se renueve la cuota y relanza el mismo comando: se reanuda. |
| Capítulos mal divididos | Edita `libro.trabajo/perfil.json`, borra `bloques/` y relanza. |
| Un bloque se ve mal | Borra su archivo en `libro.trabajo/bloques/` y relanza, quizá con `--paginas-por-bloque 3`. |
| `quedan avisos tras N revisiones` | El bloque se incluye igualmente; revísalo en el EPUB o regenéralo con más `--revisiones`. |
| Páginas escaneadas vacías | No uses `--sin-vision`: esas páginas se leen desde la imagen. |

## Privacidad y derechos de autor

- El contenido del PDF (texto e imágenes de las páginas) se envía a Anthropic para que
  Claude lo procese, igual que cualquier conversación con Claude Code. No conviertas
  documentos que no puedas compartir con un servicio externo.
- Usa esta herramienta solo con libros que tengas derecho a convertir (por ejemplo, para
  leer en tu propio lector un libro que has comprado). No distribuyas los EPUB generados
  de obras protegidas.
