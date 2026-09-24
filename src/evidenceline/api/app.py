"""The web API: the website's live question box and the hosted, read-only MCP connector.

Endpoints:

- ``GET /api/health``: whether the service is up, whether live answers are on, and the limits in force.
- ``POST /api/ask`` with ``{"question": "..."}`` (and ``"turnstile_token"`` when Turnstile is on): the
  :class:`~evidenceline.answer.models.AnswerResult` for the question.
- ``/mcp``: the same nine MCP tools as the stdio server, over Streamable HTTP, stateless, built from
  ``server.build_tools()`` so the guard rails (read-only, strict arguments, redaction) are the same code.

Protection: CORS limited to the configured origins; per-IP limits on questions and on MCP requests; a global
daily brake on model calls; optional Cloudflare Turnstile; a 500-character question limit; a 64 KB cap on request
bodies, checked before a body is parsed (:mod:`evidenceline.api.gate`); and, when ``EVIDENCELINE_PROXY_SECRET`` is
set, only requests that carry it in ``x-evidenceline-proxy-secret`` (from the website's proxy) are served on /api/ask
and /mcp, so the client IP header the proxy sets can be trusted. Question text is never logged: the log has counts
only.

Redaction: the identifier file named by ``EVIDENCELINE_REDACT`` (on the hosted demo, ``builtin:fds01-demo``, the
fictional FDS-01 client name and site address) is applied to the MCP tools by the tools themselves, and to each
question before the answer pipeline sees it. If that setting names a file that cannot be loaded, every tool returns
an error and /api/ask answers 503: nothing runs without the identifiers someone meant to load.

Run: ``python -m evidenceline.api`` or ``uvicorn --factory evidenceline.api.app:create_app``.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from evidenceline import redact
from evidenceline import server as stdio_server
from evidenceline.answer import AnswerResult, AnthropicClient, ModelClient, ModelPausedError, ModelReply, answer
from evidenceline.answer.names import find_names
from evidenceline.api import limits
from evidenceline.api.gate import NOT_THROUGH_PROXY, Gate, through_proxy
from evidenceline.api.settings import Settings, from_env
from evidenceline.api.turnstile import CloudflareTurnstile, TurnstileVerifier
from evidenceline.errors import EvidencelineError
from evidenceline.guidance.search import default_index_path
from evidenceline.redact import RedactionConfig, Redactor
from evidenceline.tooling import GuardedServer

logger = logging.getLogger("evidenceline.api")

VERSION = "0.1.0"
HOSTED_INTRO = (
    "This is the hosted, read-only copy of Evidenceline. It reads only the packaged fictional site (FDS-01) and the "
    "index of public guidance: it cannot see files on your computer."
)
PLACEHOLDERS_FORGOTTEN = "Placeholders are forgotten when the service restarts."
OWN_COMPUTER = "To redact your own client names, run Evidenceline on your own computer with your own identifier file."
REDACTION_BROKEN = (
    "Questions are paused: this server's redaction settings could not be loaded, and it does not run without them."
)
QUESTION_REDACTED = (
    " identifier(s) in the question were replaced with placeholders before it was searched or sent to a model."
)
"""The end of the answer pipeline's own note on question redactions (``pipeline._Question``), after the count."""


def hosted_note(config: RedactionConfig | None) -> str:
    """What the hosted connector redacts, for its instructions. Promises only what ``config`` can do.

    ``config`` None means the identifier file named in the environment could not be loaded.
    """
    if config is None:
        redaction = (
            "The identifier file this server is set to load could not be read, so every tool returns an error until "
            "the setting is fixed."
        )
        return f"\n{HOSTED_INTRO} {redaction}"
    builtin = redact.BUILTIN_FILES.get(config.builtin) if config.builtin is not None else None
    if builtin is not None:
        redaction = (
            f"It loads {builtin.description}: those names, and standard address, lot, email and phone formats, "
            "become placeholders such as [CLIENT-1] or [ADDRESS-1]. Any other client, site or people's names are NOT "
            "redacted."
        )
    elif config.identities:
        redaction = (
            "An identifier file is loaded: the names it lists, and standard address, lot, email and phone formats, "
            "become placeholders such as [CLIENT-1] or [ADDRESS-1]. Names it does not list are NOT redacted."
        )
    elif config.file_loaded:
        redaction = (
            "An identifier file is loaded but lists no names, so client, site and people's names are not redacted; "
            "only standard address, lot, email and phone formats become placeholders such as [ADDRESS-1]."
        )
    else:
        redaction = (
            "No identifier file is loaded here, so client, site and people's names are not redacted; only standard "
            "address, lot, email and phone formats become placeholders such as [ADDRESS-1]."
        )
    return f"\n{HOSTED_INTRO} {redaction} {PLACEHOLDERS_FORGOTTEN} {OWN_COMPUTER}"


def load_identifiers() -> RedactionConfig | None:
    """The identifier file named by the environment, or None when it is set but cannot be loaded."""
    try:
        return redact.load_config()
    except EvidencelineError:
        return None


ClientFactory = Callable[[], ModelClient | None]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    turnstile_token: str | None = None


def build_mcp_server(identifiers: RedactionConfig | None) -> GuardedServer:
    """A fresh MCP server with the same tools and instructions as the stdio server (``server.build_tools``).

    ``identifiers`` (from :func:`load_identifiers`) only shapes the note on redaction in the instructions; the tools
    load the identifier file themselves (:func:`evidenceline.redact.session`), from the same environment.
    """
    return GuardedServer(
        name=stdio_server.mcp.name,
        title=stdio_server.mcp.title,
        description=stdio_server.mcp.description,
        instructions=stdio_server.INSTRUCTIONS + hosted_note(identifiers),
        version=VERSION,
        tools=stdio_server.build_tools(),
    )


def client_ip(headers: Mapping[str, str], peer: str | None, header: str | None) -> str:
    """The caller's IP: from the configured proxy header (right-most X-Forwarded-For entry), else the socket."""
    if header:
        raw = headers.get(header, "")
        value = raw.split(",")[-1].strip() if header == "x-forwarded-for" else raw.strip()
        if value:
            return value
    return peer or "unknown"


__all__ = ["NOT_THROUGH_PROXY", "client_ip", "create_app", "hosted_note", "through_proxy"]


def redact_question(question: str, identifiers: RedactionConfig) -> tuple[str, int]:
    """``question`` with the listed identifiers, the names found by shape and the built-in patterns replaced, and
    how many were replaced.

    One pass with a fresh redactor and no audit log, so placeholders are numbered once ([CLIENT-1] for a listed name,
    [CLIENT-2] for a name found by shape) and never carry over between visitors. The answer pipeline's own name
    search then finds nothing left to replace.
    """
    counts: Counter[str] = Counter()
    config = RedactionConfig((*identifiers.identities, *find_names(question)), identifiers.status)
    text = Redactor(config, None).redact_text(question, counts)
    return text, sum(counts.values())


def count_redactions(result: AnswerResult, earlier: int) -> AnswerResult:
    """Add ``earlier`` replacements (made before the pipeline ran) to the result's count and to its note."""
    if not earlier:
        return result
    total = result.question_redactions + earlier
    notes = [note for note in result.notes if not note.endswith(QUESTION_REDACTED)]
    return result.model_copy(update={"question_redactions": total, "notes": [f"{total}{QUESTION_REDACTED}", *notes]})


def _error(status: int, message: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    body: dict[str, Any] = {"error": message}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
        body["retry_after"] = retry_after
    return JSONResponse(body, status_code=status, headers=headers)


@dataclass(slots=True)
class _BudgetedClient:
    """Counts every model call against the global daily brake, and pauses live answers once it is reached."""

    inner: ModelClient
    budget: limits.RateLimiter
    per_day: int

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    def complete(self, system: str, prompt: str) -> ModelReply:
        if self.budget.check("all") is not None:
            raise ModelPausedError(
                f"Live answers are paused: today's limit of {self.per_day} answers has been reached. They restart "
                "within 24 hours."
            )
        return self.inner.complete(system, prompt)


class _LimitedMcp:
    """The MCP endpoint behind a per-IP request limit."""

    def __init__(self, app: ASGIApp, limiter: limits.RateLimiter, header: str | None, secret: str | None) -> None:
        self._app = app
        self._limiter = limiter
        self._header = header
        self._secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            if not through_proxy(headers, self._secret):
                await _error(403, NOT_THROUGH_PROXY)(scope, receive, send)
                return
            peer = scope.get("client")
            ip = client_ip(headers, peer[0] if peer else None, self._header)
            refusal = self._limiter.check(ip)
            if refusal is not None:
                response = _error(429, f"Too many requests: the limit is {refusal.window.name}.", refusal.retry_after)
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    *,
    client_factory: ClientFactory = AnthropicClient.from_env,
    turnstile: TurnstileVerifier | None = None,
    clock: Callable[[], float] | None = None,
    index_path: Path | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings, a fake model client, a fake Turnstile and a fake clock."""
    config = settings if settings is not None else from_env()
    ip_header = config.client_ip_header if config.trust_client_ip_header else None
    tick = clock or time.monotonic
    inner = client_factory()
    budget = limits.RateLimiter([limits.Window(config.answers_per_day, limits.DAY, "answers per day")], tick)
    model = _BudgetedClient(inner, budget, config.answers_per_day) if inner is not None else None
    asks = limits.RateLimiter(
        [
            limits.Window(config.ask_per_hour, limits.HOUR, f"{config.ask_per_hour} questions per hour"),
            limits.Window(config.ask_per_day, limits.DAY, f"{config.ask_per_day} questions per day"),
        ],
        tick,
    )
    mcp_limit = limits.RateLimiter(
        [limits.Window(config.mcp_per_hour, limits.HOUR, f"{config.mcp_per_hour} requests per hour")], tick
    )
    verifier = turnstile
    if verifier is None and config.turnstile_secret:
        verifier = CloudflareTurnstile(config.turnstile_secret)
    statuses: Counter[str] = Counter()
    identifiers = load_identifiers()

    mcp = build_mcp_server(identifiers)
    # A public, read-only, credential-free server: DNS-rebinding protection guards servers bound to localhost.
    mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Evidenceline API",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        Gate, paths=("/api/ask", "/mcp"), secret=config.proxy_secret, max_body_bytes=config.max_body_bytes
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "mcp-protocol-version", "mcp-session-id", "last-event-id"],
        max_age=600,
    )
    endpoint = _LimitedMcp(StreamableHTTPASGIApp(mcp.session_manager), mcp_limit, ip_header, config.proxy_secret)
    app.router.routes.append(Route("/mcp", endpoint=endpoint))

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, __: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return _error(400, 'Send JSON such as {"question": "What is a conceptual site model?"}.')

    @app.get("/api/health")
    async def health() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return {
            "status": "ok",
            "version": VERSION,
            "live_answers": model is not None,
            "model": model.model_id if model is not None else None,
            "index": (index_path or default_index_path()).exists(),
            "turnstile": verifier is not None,
            "proxy_only": config.proxy_secret is not None,
            "client_ip_from": ip_header or "connection",
            "redaction": identifiers.status if identifiers is not None else "The identifier file could not be loaded.",
            "limits": {
                "questions_per_hour": config.ask_per_hour,
                "questions_per_day": config.ask_per_day,
                "answers_per_day": config.answers_per_day,
                "question_characters": config.max_question_chars,
                "request_bytes": config.max_body_bytes,
                "mcp_requests_per_hour": config.mcp_per_hour,
            },
        }

    @app.post("/api/ask", response_model=AnswerResult)
    async def ask(body: AskRequest, request: Request, response: Response) -> AnswerResult | JSONResponse:  # pyright: ignore[reportUnusedFunction]
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not through_proxy(headers, config.proxy_secret):
            return _error(403, NOT_THROUGH_PROXY)
        if identifiers is None:
            return _error(503, REDACTION_BROKEN)
        question = body.question.strip()
        if not question:
            return _error(400, "The question is empty.")
        if len(question) > config.max_question_chars:
            return _error(
                400,
                f"The question is {len(question)} characters long; the limit is {config.max_question_chars}. "
                "Ask one thing at a time.",
            )
        peer = request.client.host if request.client else None
        ip = client_ip(headers, peer, ip_header)
        # The limit comes before Turnstile, so a flood of bad tokens from one address is refused without a call to
        # Cloudflare for each one. A failed check therefore uses one of that address's questions.
        refusal = asks.check(ip)
        if refusal is not None:
            return _error(429, f"Too many questions: the limit is {refusal.window.name}.", refusal.retry_after)
        if verifier is not None and not (body.turnstile_token and await verifier.verify(body.turnstile_token, ip)):
            return _error(403, "The check that you are not a robot did not pass. Reload the page and try again.")
        earlier = 0
        if identifiers.identities:
            question, earlier = redact_question(question, identifiers)
        result = await anyio.to_thread.run_sync(partial(answer, question, model, index_path=index_path))
        result = count_redactions(result, earlier)
        response.headers["Cache-Control"] = "no-store"
        statuses[result.status] += 1
        logger.info("ask status=%s totals=%s", result.status, dict(statuses))
        return result

    return app
