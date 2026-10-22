"""pdftoepub: convierte libros PDF (especialmente técnicos) a EPUB con agentes de IA."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _rango(texto: str) -> tuple[int, int]:
    a, _, b = texto.partition("-")
    return int(a), int(b or a)


def main() -> None:
    from .llm import MODELO_POR_DEFECTO, MOTORES, PRECIOS, ErrorMotor
    from .orquestador import Conversor, Opciones, estimar

    ap = argparse.ArgumentParser(
        prog="pdftoepub",
        description="Convierte un libro PDF en un EPUB limpio y coherente usando agentes de IA (Claude).",
    )
    ap.add_argument("pdf", type=Path, help="libro PDF de entrada")
    ap.add_argument("-o", "--salida", type=Path, help="EPUB de salida (por defecto, junto al PDF)")
    ap.add_argument("--motor", default="claude-code", choices=list(MOTORES),
                    help="claude-code: usa tu Claude Code local (por defecto); api: usa ANTHROPIC_API_KEY")
    ap.add_argument("--modelo", default=MODELO_POR_DEFECTO, help=f"modelo de Claude (por defecto {MODELO_POR_DEFECTO})")
    ap.add_argument("--esfuerzo", default="high", choices=["low", "medium", "high", "xhigh", "max"],
                    help="profundidad de razonamiento de los agentes (por defecto high)")
    ap.add_argument("--paginas-por-bloque", type=int, default=6, help="páginas que procesa el agente en cada llamada")
    ap.add_argument("--paralelo", type=int, default=3, help="capítulos convertidos en paralelo")
    ap.add_argument("--revisiones", type=int, default=2, help="máximo de rondas del agente Revisor por bloque")
    ap.add_argument("--rango", type=_rango, help="convertir solo un rango de páginas, p. ej. 1-40")
    ap.add_argument("--sin-vision", action="store_true",
                    help="no enviar las páginas como PDF al modelo (más barato, peor con tablas y escaneos)")
    ap.add_argument("--sin-resaltado", action="store_true", help="no colorear la sintaxis del código")
    ap.add_argument("--sin-ia", action="store_true", help="conversión básica sin IA (para probar)")
    ap.add_argument("--estimar", action="store_true", help="solo estimar el consumo de tokens y salir")
    args = ap.parse_args()

    if not args.pdf.is_file():
        ap.error(f"no existe el archivo {args.pdf}")
    salida = args.salida or args.pdf.with_suffix(".epub")
    opciones = Opciones(
        paginas_por_bloque=args.paginas_por_bloque,
        vision=not args.sin_vision,
        paralelo=args.paralelo,
        revisiones=args.revisiones,
        resaltar=not args.sin_resaltado,
        sin_ia=args.sin_ia,
        rango=args.rango,
    )

    if args.estimar:
        print(estimar(args.pdf, opciones))
        if args.modelo in PRECIOS:
            print(f"Precio de {args.modelo}: {PRECIOS[args.modelo][0]} / {PRECIOS[args.modelo][1]} USD por M de tokens (entrada/salida)")
        return

    claude = None
    if not args.sin_ia:
        try:
            claude = MOTORES[args.motor](args.modelo, args.esfuerzo)
        except ErrorMotor as e:
            sys.exit(str(e))

    try:
        Conversor(args.pdf, salida, opciones, claude).ejecutar()
    except KeyboardInterrupt:
        sys.exit("\nInterrumpido. Vuelve a lanzar el mismo comando para reanudar.")
    except ErrorMotor as e:
        sys.exit(f"{e}\nEl trabajo hecho está guardado: relanza el comando para reanudar.")
