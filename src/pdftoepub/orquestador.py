"""Orquestador: coordina extracción, agentes, verificación y ensamblado.

Todo el trabajo intermedio se guarda en una carpeta de trabajo, de modo que si la
conversión se interrumpe, al relanzarla se reanuda donde se quedó. También permite
editar a mano `perfil.json` (p. ej. para corregir capítulos) y volver a ejecutar.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from . import agentes, ensamblador, extractor, verificador
from .agentes import Capitulo, Perfil
from .extractor import Documento, Pagina
from .llm import ClaudeCode, Motor, RespuestaRechazada, RespuestaTruncada

LIMITE_BLOQUE_PDF = 24 * 1024 * 1024  # bytes; la API admite 32 MB por petición
LIMITE_TEXTO_BLOQUE = 45_000  # caracteres de texto extraído por bloque


@dataclass
class Opciones:
    paginas_por_bloque: int = 6
    vision: bool = True
    paralelo: int = 3
    revisiones: int = 2
    resaltar: bool = True
    sin_ia: bool = False
    rango: tuple[int, int] | None = None


class Registro:
    def __init__(self):
        self._lock = threading.Lock()

    def __call__(self, mensaje: str) -> None:
        with self._lock:
            print(mensaje, file=sys.stderr, flush=True)


log = Registro()


def _bloques(paginas: list[Pagina], tamano: int) -> list[list[Pagina]]:
    bloques, actual, chars = [], [], 0
    for p in paginas:
        if actual and (len(actual) >= tamano or chars + len(p.texto) > LIMITE_TEXTO_BLOQUE):
            bloques.append(actual)
            actual, chars = [], 0
        actual.append(p)
        chars += len(p.texto)
    if actual:
        bloques.append(actual)
    return bloques


class Conversor:
    def __init__(self, pdf: Path, salida: Path, opciones: Opciones, claude: Motor | None):
        self.pdf = pdf
        self.salida = salida
        self.op = opciones
        self.claude = claude
        self.trabajo = salida.with_suffix(".trabajo")
        self.dir_img = self.trabajo / "imagenes"
        self.dir_bloques = self.trabajo / "bloques"
        self.dir_bloques.mkdir(parents=True, exist_ok=True)

    # -- pasos ---------------------------------------------------------------

    def perfil(self, doc: Documento) -> Perfil:
        ruta = self.trabajo / "perfil.json"
        if ruta.exists():
            log(f"· Usando el análisis guardado en {ruta}")
            return Perfil.desde_dict(json.loads(ruta.read_text()))
        if self.claude is None:
            perfil = self._perfil_sin_ia(doc)
        else:
            log(f"· Agente Analista ({self.claude.nombre}): estudiando la estructura del libro…")
            perfil = agentes.analizar(self.claude, doc)
        ruta.write_text(json.dumps(perfil.a_dict(), ensure_ascii=False, indent=2))
        return perfil

    def _perfil_sin_ia(self, doc: Documento) -> Perfil:
        primera, ultima = doc.paginas[0].numero, doc.paginas[-1].numero
        caps = [Capitulo(t, p, p) for n, t, p in doc.indice_pdf if n == 1 and primera <= p <= ultima]
        caps = agentes._normalizar_capitulos(caps, primera, ultima)
        meta = doc.metadatos
        return Perfil(meta.get("title") or self.pdf.stem, [meta["author"]] if meta.get("author") else [],
                      "es", "", "", "", [], "", caps)

    def convertir_capitulo(self, i: int, cap: Capitulo, doc: Documento, perfil: Perfil) -> list[str] | None:
        if cap.omitir:
            return None
        paginas = [p for p in doc.paginas if cap.inicio <= p.numero <= cap.fin]
        fragmentos: list[str] = []
        for bloque in _bloques(paginas, self.op.paginas_por_bloque):
            fragmentos.extend(self._convertir_bloque(i, cap, bloque, perfil, fragmentos, bloque[0].numero == cap.inicio))
        log(f"  ✓ Capítulo {i}: {cap.titulo}")
        return fragmentos

    def _convertir_bloque(self, i, cap, paginas, perfil, previos, inicio_cap) -> list[str]:
        a, b = paginas[0].numero, paginas[-1].numero
        cache = self.dir_bloques / f"cap{i:03d}_p{a:04d}-{b:04d}.html"
        if cache.exists():
            return [cache.read_text()]

        if self.claude is None:
            xhtml = ensamblador.conversion_basica(paginas)
            cache.write_text(xhtml)
            return [xhtml]

        pdf_bytes = extractor.subpdf(self.pdf, a, b) if self.op.vision else None
        if pdf_bytes and len(pdf_bytes) > LIMITE_BLOQUE_PDF:
            if len(paginas) > 1:
                mitad = len(paginas) // 2
                primera = self._convertir_bloque(i, cap, paginas[:mitad], perfil, previos, inicio_cap)
                return primera + self._convertir_bloque(i, cap, paginas[mitad:], perfil, previos + primera, False)
            log(f"  ! Página {a} demasiado pesada para enviarla como PDF; se usa solo el texto.")
            pdf_bytes = None

        ids = {r.id for p in paginas for r in p.recursos}
        anterior = previos[-1] if previos else ""
        try:
            xhtml = agentes.maquetar(self.claude, perfil, cap, paginas, pdf_bytes, anterior, inicio_cap)
            for intento in range(self.op.revisiones + 1):
                problemas = verificador.comprobar(xhtml, paginas, ids)
                if not problemas:
                    break
                if intento == self.op.revisiones:
                    log(f"  ! Págs. {a}-{b}: quedan avisos tras {intento} revisiones: {problemas[0][:150]}")
                    break
                log(f"  · Agente Revisor corrigiendo págs. {a}-{b}: {problemas[0][:120]}")
                xhtml = agentes.revisar(self.claude, perfil, paginas, pdf_bytes, xhtml, problemas)
        except RespuestaTruncada:
            if len(paginas) == 1:
                raise
            mitad = len(paginas) // 2
            log(f"  · Págs. {a}-{b}: salida demasiado larga, se divide el bloque.")
            primera = self._convertir_bloque(i, cap, paginas[:mitad], perfil, previos, inicio_cap)
            return primera + self._convertir_bloque(i, cap, paginas[mitad:], perfil, previos + primera, False)
        except RespuestaRechazada as e:
            log(f"  ! Págs. {a}-{b}: el modelo rechazó el contenido ({e}); se usa conversión básica.")
            xhtml = ensamblador.conversion_basica(paginas, f"[Páginas {a}–{b}: conversión básica sin IA]")

        cache.write_text(xhtml)
        return [xhtml]

    # -- principal -----------------------------------------------------------

    def ejecutar(self) -> None:
        log(f"· Extrayendo texto, código e imágenes de {self.pdf.name}…")
        doc = extractor.extraer(self.pdf, self.dir_img, self.op.rango)
        escaneadas = sum(p.sin_texto for p in doc.paginas)
        log(f"  {len(doc.paginas)} páginas, cuerpo de {doc.tamano_cuerpo:g}pt, "
            f"{sum(len(p.recursos) for p in doc.paginas)} imágenes/diagramas"
            + (f", {escaneadas} páginas sin texto (se leerán con visión)" if escaneadas else ""))
        if escaneadas and (self.claude is None or not self.op.vision):
            log("  ! Hay páginas escaneadas y la visión está desactivada: esas páginas quedarán vacías.")

        perfil = self.perfil(doc)
        activos = [c for c in perfil.capitulos if not c.omitir]
        log(f"· «{perfil.titulo}» — {len(activos)} capítulos ({perfil.tematica or 'temática no detectada'})")
        log("· Agente Maquetador convirtiendo capítulos…")

        resultados: dict[int, list[str] | None] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.op.paralelo)) as pool:
            futuros = {pool.submit(self.convertir_capitulo, i, c, doc, perfil): i
                       for i, c in enumerate(perfil.capitulos, 1)}
            for futuro in as_completed(futuros):
                resultados[futuros[futuro]] = futuro.result()

        log("· Ensamblando EPUB…")
        finales = []
        for i, cap in enumerate(perfil.capitulos, 1):
            fragmentos = resultados.get(i)
            if not fragmentos:
                continue
            cuerpo = ensamblador.unir_bloques(fragmentos)
            finales.append(ensamblador.preparar_capitulo(cap.titulo, i, cuerpo, "imagenes", self.op.resaltar))

        portada = extractor.portada(self.pdf, self.trabajo / "portada.png")
        ensamblador.construir_epub(perfil, finales, self.dir_img, portada, self.salida, self.op.resaltar)
        log(f"✔ EPUB generado: {self.salida}")
        if self.claude:
            c = self.claude.consumo
            coste = c.coste(self.claude.modelo)
            if coste is None:
                texto_coste = ""
            elif isinstance(self.claude, ClaudeCode):
                texto_coste = f" · equivalente a ≈ {coste:.2f} USD de API (con suscripción se descuenta de tu cuota de uso)"
            else:
                texto_coste = f" · ≈ {coste:.2f} USD"
            log(f"  {c.llamadas} llamadas a {self.claude.nombre} · {c.entrada:,} tokens de entrada · "
                f"{c.cache_leida:,} de caché · {c.salida:,} de salida{texto_coste}")


def estimar(pdf: Path, opciones: Opciones) -> str:
    doc = pymupdf.open(pdf)
    inicio, fin = opciones.rango or (1, doc.page_count)
    n = min(fin, doc.page_count) - inicio + 1
    chars = sum(len(doc[i].get_text()) for i in range(inicio - 1, min(fin, doc.page_count)))
    tokens_texto = chars / 3.5
    # Cada página como PDF cuesta además ~1.600 tokens de imagen; la salida ≈ texto + marcado.
    entrada = tokens_texto + (n * 1600 if opciones.vision else 0) + n * 150
    salida = tokens_texto * 1.25
    return f"{n} páginas · ~{entrada / 1e6:.2f} M tokens de entrada · ~{salida / 1e6:.2f} M de salida (sin contar revisiones)"
