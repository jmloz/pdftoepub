"""Motores de IA compartidos por todos los agentes.

Hay dos formas de hablar con Claude, con la misma interfaz (`pedir` / `pedir_json`):

- `ClaudeCode` (por defecto): ejecuta el `claude` instalado en local en modo no
  interactivo (`claude -p`), usando la sesión y la suscripción de Claude Code.
- `ClaudeAPI`: llama directamente a la API de Anthropic con `ANTHROPIC_API_KEY`
  (facturación por tokens).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field

MODELO_POR_DEFECTO = "claude-opus-5-5"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
MAX_TOKENS_SALIDA = 64000
TIEMPO_MAXIMO = 30 * 60  # segundos por llamada

# USD por millón de tokens (entrada, salida). Solo para la estimación que se muestra al final.
PRECIOS = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class RespuestaTruncada(Exception):
    """La salida superó el máximo de tokens: el bloque debe dividirse."""


class RespuestaRechazada(Exception):
    pass


class ErrorMotor(Exception):
    """Fallo del motor de IA (sin sesión, límite de uso, error de red…)."""


@dataclass
class Consumo:
    entrada: int = 0
    salida: int = 0
    cache_leida: int = 0
    llamadas: int = 0
    coste_informado: float = 0.0  # lo que reporta Claude Code
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def sumar(self, entrada: int, salida: int, cache_leida: int, coste: float = 0.0) -> None:
        with self._lock:
            self.entrada += entrada
            self.salida += salida
            self.cache_leida += cache_leida
            self.coste_informado += coste
            self.llamadas += 1

    def coste(self, modelo: str) -> float | None:
        if self.coste_informado:
            return self.coste_informado
        if modelo not in PRECIOS:
            return None
        p_in, p_out = PRECIOS[modelo]
        return (self.entrada * p_in + self.cache_leida * p_in * 0.1 + self.salida * p_out) / 1e6


def bloque_pdf(datos: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(datos).decode("ascii"),
        },
    }


def _como_bloques(contenido: list[dict] | str) -> list[dict]:
    return [{"type": "text", "text": contenido}] if isinstance(contenido, str) else contenido


class Motor:
    nombre = ""

    def __init__(self, modelo: str = MODELO_POR_DEFECTO, esfuerzo: str = "high"):
        self.modelo = modelo
        self.esfuerzo = esfuerzo
        self.consumo = Consumo()

    def pedir(self, sistema: str, contenido: list[dict] | str, *, esquema: dict | None = None,
              esfuerzo: str | None = None) -> str:
        raise NotImplementedError

    def pedir_json(self, sistema: str, contenido: list[dict] | str, esquema: dict, **kw) -> dict:
        return json.loads(self.pedir(sistema, contenido, esquema=esquema, **kw))


# ---------------------------------------------------------------------------
# Claude Code local
# ---------------------------------------------------------------------------

class ClaudeCode(Motor):
    """Ejecuta los agentes con el CLI `claude` de Claude Code instalado en local."""

    nombre = "Claude Code"

    def __init__(self, modelo: str = MODELO_POR_DEFECTO, esfuerzo: str = "high", ejecutable: str = "claude"):
        super().__init__(modelo, esfuerzo)
        ruta = shutil.which(ejecutable)
        if ruta is None:
            raise ErrorMotor(
                "No se encuentra el comando `claude`. Instala Claude Code "
                "(https://claude.com/claude-code) e inicia sesión ejecutando `claude` una vez."
            )
        self.ejecutable = ruta

    def _comando(self, sistema: str, esquema: dict | None, esfuerzo: str) -> list[str]:
        cmd = [
            self.ejecutable, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose",
            "--model", self.modelo,
            "--effort", esfuerzo,
            "--system-prompt", sistema,
            # Los agentes solo transforman texto: sin herramientas, sin MCP, sin
            # configuración del usuario y sin guardar la sesión en el historial.
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
        ]
        if esquema:
            cmd += ["--json-schema", json.dumps(esquema)]
        return cmd

    def _ejecutar(self, sistema: str, contenido: list[dict] | str, esquema: dict | None, esfuerzo: str) -> dict:
        mensaje = {"type": "user", "message": {"role": "user", "content": _como_bloques(contenido)}}
        entorno = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(MAX_TOKENS_SALIDA)}
        # Directorio vacío: evita que Claude Code cargue CLAUDE.md u otros ficheros del proyecto.
        with tempfile.TemporaryDirectory(prefix="pdftoepub-") as vacio:
            try:
                proc = subprocess.run(
                    self._comando(sistema, esquema, esfuerzo),
                    input=json.dumps(mensaje) + "\n",
                    capture_output=True, text=True, cwd=vacio, env=entorno, timeout=TIEMPO_MAXIMO,
                )
            except subprocess.TimeoutExpired as e:
                raise ErrorMotor(f"Claude Code no respondió en {TIEMPO_MAXIMO // 60} minutos.") from e

        resultado = None
        for linea in proc.stdout.splitlines():
            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if evento.get("type") == "result":
                resultado = evento
        if resultado is None:
            detalle = (proc.stderr or proc.stdout).strip()[-800:]
            raise ErrorMotor(f"Claude Code terminó sin respuesta (código {proc.returncode}): {detalle}")
        return resultado

    def pedir(self, sistema, contenido, *, esquema=None, esfuerzo=None) -> str:
        r = self._ejecutar(sistema, contenido, esquema, esfuerzo or self.esfuerzo)
        uso = r.get("usage") or {}
        self.consumo.sumar(
            (uso.get("input_tokens") or 0) + (uso.get("cache_creation_input_tokens") or 0),
            uso.get("output_tokens") or 0,
            uso.get("cache_read_input_tokens") or 0,
            r.get("total_cost_usd") or 0.0,
        )
        if r.get("stop_reason") == "refusal":
            raise RespuestaRechazada(r.get("result") or "El modelo rechazó procesar este fragmento.")
        if r.get("stop_reason") == "max_tokens":
            raise RespuestaTruncada()
        if r.get("is_error") or r.get("subtype") != "success":
            raise ErrorMotor(f"Claude Code devolvió un error: {r.get('result') or r.get('subtype')}")
        if esquema:
            if r.get("structured_output") is None:
                raise ErrorMotor("Claude Code no devolvió la salida estructurada esperada.")
            return json.dumps(r["structured_output"])
        return r.get("result") or ""


# ---------------------------------------------------------------------------
# API de Anthropic
# ---------------------------------------------------------------------------

class ClaudeAPI(Motor):
    """Llama a la API de Anthropic directamente (requiere ANTHROPIC_API_KEY)."""

    nombre = "API de Anthropic"

    def __init__(self, modelo: str = MODELO_POR_DEFECTO, esfuerzo: str = "high"):
        super().__init__(modelo, esfuerzo)
        import anthropic

        self._anthropic = anthropic
        self.cliente = anthropic.Anthropic(max_retries=5)

    def pedir(self, sistema, contenido, *, esquema=None, esfuerzo=None) -> str:
        output_config: dict = {"effort": esfuerzo or self.esfuerzo}
        if esquema:
            output_config["format"] = {"type": "json_schema", "schema": esquema}

        try:
            with self.cliente.beta.messages.stream(
                model=self.modelo,
                max_tokens=MAX_TOKENS_SALIDA,
                # El system prompt de cada agente es fijo: se cachea entre bloques.
                system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": _como_bloques(contenido)}],
                output_config=output_config,
                # Libros de seguridad/hacking pueden activar los clasificadores: si el modelo
                # rechaza la petición, la API la reintenta con el modelo alternativo recomendado.
                betas=[BETA_FALLBACK],
                fallbacks="default",
            ) as stream:
                respuesta = stream.get_final_message()
        except TypeError as e:
            if "authentication" in str(e):
                raise ErrorMotor("No hay credenciales de la API. Define ANTHROPIC_API_KEY.") from e
            raise
        except self._anthropic.AuthenticationError as e:
            raise ErrorMotor("Clave de API no válida. Revisa ANTHROPIC_API_KEY.") from e
        except self._anthropic.APIError as e:
            raise ErrorMotor(f"Error de la API de Anthropic: {e}") from e

        uso = respuesta.usage
        self.consumo.sumar(
            (uso.input_tokens or 0) + (uso.cache_creation_input_tokens or 0),
            uso.output_tokens or 0,
            uso.cache_read_input_tokens or 0,
        )
        if respuesta.stop_reason == "refusal":
            detalle = getattr(respuesta.stop_details, "explanation", None) if respuesta.stop_details else None
            raise RespuestaRechazada(detalle or "El modelo rechazó procesar este fragmento.")
        if respuesta.stop_reason == "max_tokens":
            raise RespuestaTruncada()
        return "".join(b.text for b in respuesta.content if b.type == "text")


MOTORES = {"claude-code": ClaudeCode, "api": ClaudeAPI}
