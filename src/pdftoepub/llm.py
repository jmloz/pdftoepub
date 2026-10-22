"""AI engines shared by all agents.

There are two ways to talk to Claude, with the same interface (`ask` / `ask_json`):

- `ClaudeCode` (default): runs the locally installed `claude` CLI in
  non-interactive mode (`claude -p`), using your Claude Code login and plan.
- `ClaudeAPI`: calls the Anthropic API directly with `ANTHROPIC_API_KEY`
  (billed per token).
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

from .i18n import t

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_OUTPUT_TOKENS = 64000
TIMEOUT = 30 * 60  # seconds per call

# USD per million tokens (input, output). Only used for the summary shown at the end.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class TruncatedResponse(Exception):
    """The output exceeded the token limit: the chunk must be split."""


class RefusedResponse(Exception):
    pass


class EngineError(Exception):
    """AI engine failure (not logged in, usage limit, network error…)."""


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    calls: int = 0
    reported_cost: float = 0.0  # as reported by Claude Code
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, input: int, output: int, cache_read: int, cost: float = 0.0) -> None:
        with self._lock:
            self.input += input
            self.output += output
            self.cache_read += cache_read
            self.reported_cost += cost
            self.calls += 1

    def cost(self, model: str) -> float | None:
        if self.reported_cost:
            return self.reported_cost
        if model not in PRICES:
            return None
        p_in, p_out = PRICES[model]
        return (self.input * p_in + self.cache_read * p_in * 0.1 + self.output * p_out) / 1e6


def pdf_block(data: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


def _as_blocks(content: list[dict] | str) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


class Engine:
    name = ""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "high"):
        self.model = model
        self.effort = effort
        self.usage = Usage()

    def ask(self, system: str, content: list[dict] | str, *, schema: dict | None = None,
            effort: str | None = None) -> str:
        raise NotImplementedError

    def ask_json(self, system: str, content: list[dict] | str, schema: dict, **kw) -> dict:
        return json.loads(self.ask(system, content, schema=schema, **kw))


# ---------------------------------------------------------------------------
# Local Claude Code
# ---------------------------------------------------------------------------

class ClaudeCode(Engine):
    """Runs the agents through the locally installed Claude Code CLI (`claude`)."""

    name = "Claude Code"

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "high", executable: str = "claude"):
        super().__init__(model, effort)
        path = shutil.which(executable)
        if path is None:
            raise EngineError(t("err.no_claude"))
        self.executable = path

    def _command(self, system: str, schema: dict | None, effort: str) -> list[str]:
        cmd = [
            self.executable, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose",
            "--model", self.model,
            "--effort", effort,
            "--system-prompt", system,
            # The agents only transform text: no tools, no MCP servers, no user
            # settings, and the session is not saved to the user's history.
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
        ]
        if schema:
            cmd += ["--json-schema", json.dumps(schema)]
        return cmd

    def _run(self, system: str, content: list[dict] | str, schema: dict | None, effort: str) -> dict:
        message = {"type": "user", "message": {"role": "user", "content": _as_blocks(content)}}
        env = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(MAX_OUTPUT_TOKENS)}
        # Empty working directory: keeps Claude Code from loading CLAUDE.md or project files.
        with tempfile.TemporaryDirectory(prefix="pdftoepub-") as empty_dir:
            try:
                proc = subprocess.run(
                    self._command(system, schema, effort),
                    input=json.dumps(message) + "\n",
                    capture_output=True, text=True, cwd=empty_dir, env=env, timeout=TIMEOUT,
                )
            except subprocess.TimeoutExpired as e:
                raise EngineError(t("err.timeout", minutes=TIMEOUT // 60)) from e

        result = None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result = event
        if result is None:
            detail = (proc.stderr or proc.stdout).strip()[-800:]
            raise EngineError(t("err.no_result", code=proc.returncode, detail=detail))
        return result

    def ask(self, system, content, *, schema=None, effort=None) -> str:
        r = self._run(system, content, schema, effort or self.effort)
        usage = r.get("usage") or {}
        self.usage.add(
            (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0),
            usage.get("output_tokens") or 0,
            usage.get("cache_read_input_tokens") or 0,
            r.get("total_cost_usd") or 0.0,
        )
        if r.get("stop_reason") == "refusal":
            raise RefusedResponse(r.get("result") or t("err.refused"))
        if r.get("stop_reason") == "max_tokens":
            raise TruncatedResponse()
        if r.get("is_error") or r.get("subtype") != "success":
            raise EngineError(t("err.claude_code", detail=r.get("result") or r.get("subtype")))
        if schema:
            if r.get("structured_output") is None:
                raise EngineError(t("err.no_structured"))
            return json.dumps(r["structured_output"])
        return r.get("result") or ""


# ---------------------------------------------------------------------------
# Anthropic API
# ---------------------------------------------------------------------------

class ClaudeAPI(Engine):
    """Calls the Anthropic API directly (requires ANTHROPIC_API_KEY)."""

    name = "Anthropic API"

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "high"):
        super().__init__(model, effort)
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=5)

    def ask(self, system, content, *, schema=None, effort=None) -> str:
        output_config: dict = {"effort": effort or self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                # Each agent's system prompt is fixed, so it is cached across chunks.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": _as_blocks(content)}],
                output_config=output_config,
                # Security/hacking books may trigger the safety classifiers: if the model
                # refuses, the API retries the request on the recommended fallback model.
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except TypeError as e:
            if "authentication" in str(e):
                raise EngineError(t("err.no_api_key")) from e
            raise
        except self._anthropic.AuthenticationError as e:
            raise EngineError(t("err.bad_api_key")) from e
        except self._anthropic.APIError as e:
            raise EngineError(t("err.api", detail=e)) from e

        u = response.usage
        self.usage.add(
            (u.input_tokens or 0) + (u.cache_creation_input_tokens or 0),
            u.output_tokens or 0,
            u.cache_read_input_tokens or 0,
        )
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", None) if response.stop_details else None
            raise RefusedResponse(detail or t("err.refused"))
        if response.stop_reason == "max_tokens":
            raise TruncatedResponse()
        return "".join(b.text for b in response.content if b.type == "text")


ENGINES = {"claude-code": ClaudeCode, "api": ClaudeAPI}
