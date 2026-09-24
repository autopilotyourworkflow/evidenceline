"""How the answer pipeline reaches a model: one small protocol and three clients.

- :class:`AnthropicClient`: the official ``anthropic`` SDK, for the live question box. The key comes from
  ``ANTHROPIC_API_KEY`` and the model from ``EVIDENCELINE_MODEL`` (default ``claude-sonnet-5``). A spending cap,
  a billing problem or a rate limit becomes :class:`ModelPausedError`; anything else that fails becomes
  :class:`ModelFailedError`.
- :class:`ClaudeCliClient`: the local Claude Code CLI, headless, with every tool switched off. Only for preparing
  the website's example answers in advance on the owner's machine.
- :class:`FakeClient`: returns fixed text, for tests.

Every client gets the same system prompt and user prompt and returns plain text; nothing else is sent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import httpx2

MODEL_ENV = "EVIDENCELINE_MODEL"
KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 4000
"""Room for the model's thinking and a 2 or 3 sentence answer."""
EFFORT = "medium"

CLI_ENV = "EVIDENCELINE_CLAUDE_CLI"
VSCODE_CLI_DIR = "anthropic.claude-code-*/resources/native-binary"
"""Where the Claude Code extension for VS Code keeps its CLI, under ``~/.vscode/extensions``."""
CLI_NAMES = ("claude.exe", "claude")


def _vscode_cli() -> Path | None:
    """A VS Code copy of the Claude Code CLI in the user's home folder (the last by folder name), if there is one."""
    folders = sorted((Path.home() / ".vscode" / "extensions").glob(VSCODE_CLI_DIR))
    found = [folder / name for folder in folders for name in CLI_NAMES if (folder / name).is_file()]
    return found[-1] if found else None


def default_cli() -> Path:
    """``$EVIDENCELINE_CLAUDE_CLI``, else a VS Code copy of Claude Code, else ``claude`` on the PATH."""
    configured = os.environ.get(CLI_ENV, "").strip()
    if configured:
        return Path(configured)
    vscode = _vscode_cli()
    if vscode is not None:
        return vscode
    found = shutil.which("claude")
    return Path(found) if found else Path("claude")


@dataclass(frozen=True, slots=True)
class ModelReply:
    text: str
    model: str


class ModelUnavailableError(Exception):
    """The model could not answer. The message is plain English and safe to show."""


class ModelPausedError(ModelUnavailableError):
    """Live answers are paused: a spending cap, billing limit or rate limit was reached."""


class ModelFailedError(ModelUnavailableError):
    """The model call failed for another reason (network, authentication, refusal, server error)."""


class ModelClient(Protocol):
    """Anything that turns a system prompt and a user prompt into text."""

    @property
    def model_id(self) -> str: ...

    def complete(self, system: str, prompt: str) -> ModelReply: ...


# --- Anthropic API -------------------------------------------------------------------------------------------------

_PAUSE_HINTS = ("credit balance", "usage limit", "spend limit", "spending limit", "billing")
"""The API reports a workspace spending cap as a 400 invalid_request_error whose message says so; there is no
separate error type for it, so these words in the message are the only signal."""


def _is_spend_cap(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _PAUSE_HINTS)


class AnthropicClient:
    """The official Anthropic SDK. Built with :meth:`from_env`, which returns None when no key is set."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = 45.0,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """``transport`` replaces the network, for tests (``httpx2.MockTransport``)."""
        import anthropic  # noqa: PLC0415 - only the live service needs the SDK

        self._model = model
        http = httpx2.Client(transport=transport, timeout=timeout) if transport is not None else None
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1, http_client=http)

    @classmethod
    def from_env(cls) -> AnthropicClient | None:
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            return None
        return cls(key, os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL)

    @property
    def model_id(self) -> str:
        return self._model

    def complete(self, system: str, prompt: str) -> ModelReply:
        import anthropic  # noqa: PLC0415

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": EFFORT},
            )
        except anthropic.RateLimitError as exc:
            raise ModelPausedError("Live answers are busy right now (rate limit). Try again in a minute.") from exc
        except anthropic.AuthenticationError as exc:
            raise ModelFailedError("Live answers are not configured correctly (the API key was refused).") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402 or exc.type == "billing_error" or _is_spend_cap(exc.message):
                raise ModelPausedError("Live answers are paused: their spending cap has been reached.") from exc
            raise ModelFailedError(f"The model service returned an error (HTTP {exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelFailedError("The model service could not be reached.") from exc
        if response.stop_reason == "refusal":
            raise ModelFailedError("The model declined to answer this question.")
        text = "".join(block.text for block in response.content if isinstance(block, anthropic.types.TextBlock))
        return ModelReply(text=text.strip(), model=response.model or self._model)


# --- Claude Code CLI (offline precompute only) ---------------------------------------------------------------------

Runner = Callable[[list[str], str, Path], subprocess.CompletedProcess[str]]


def _run(command: list[str], stdin: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # fixed executable and arguments; the prompt goes through stdin
        command, input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=600, check=False
    )


@dataclass(slots=True)
class ClaudeCliClient:
    """The local Claude Code CLI, headless (``-p``), with every tool switched off (``--tools ""``).

    Runs in an empty temporary folder with ``--safe-mode`` (no CLAUDE.md, skills, plugins, hooks or MCP servers),
    so nothing but the system prompt and the user prompt reaches the model. The prompt goes through stdin. Uses the
    owner's Claude Code login; never used by the web service.
    """

    executable: Path = field(default_factory=default_cli)
    model: str = DEFAULT_MODEL
    runner: Runner = _run

    @property
    def model_id(self) -> str:
        return self.model

    def command(self, system: str) -> list[str]:
        return [
            str(self.executable),
            "-p",
            "--tools",
            "",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--effort",
            EFFORT,
            "--system-prompt",
            system,
            "--no-session-persistence",
            "--strict-mcp-config",
            "--safe-mode",
            "--disable-slash-commands",
        ]

    def complete(self, system: str, prompt: str) -> ModelReply:
        with tempfile.TemporaryDirectory(prefix="evidenceline-cli-") as folder:
            try:
                done = self.runner(self.command(system), prompt, Path(folder))
            except (OSError, subprocess.SubprocessError) as exc:
                raise ModelFailedError(f"The Claude Code CLI could not be run ({type(exc).__name__}).") from exc
        try:
            payload = cast(dict[str, Any], json.loads(done.stdout))
        except json.JSONDecodeError as exc:
            raise ModelFailedError(f"The Claude Code CLI did not return JSON (exit code {done.returncode}).") from exc
        status = payload.get("api_error_status")
        if status in (402, 429):
            raise ModelPausedError(f"The Claude Code CLI reported a usage limit (HTTP {status}).")
        if payload.get("is_error") or payload.get("subtype") != "success":
            raise ModelFailedError(f"The Claude Code CLI reported an error ({payload.get('subtype')}).")
        if payload.get("stop_reason") == "refusal":
            raise ModelFailedError("The model declined to answer this question.")
        usage = cast(dict[str, Any], payload.get("modelUsage") or {})
        model = self.model if self.model in usage else next((m for m in usage if "haiku" not in m), self.model)
        return ModelReply(text=str(payload.get("result", "")).strip(), model=model)


# --- tests -------------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class FakeClient:
    """Returns ``reply`` (or raises ``error``) and records every prompt it was sent."""

    reply: str = ""
    error: ModelUnavailableError | None = None
    model: str = "fake-model"
    calls: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    @property
    def model_id(self) -> str:
        return self.model

    def complete(self, system: str, prompt: str) -> ModelReply:
        self.calls.append((system, prompt))
        if self.error is not None:
            raise self.error
        return ModelReply(text=self.reply, model=self.model)
