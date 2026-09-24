"""Settings for the web API, read once from environment variables. Every limit has a safe default."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from evidenceline.errors import EvidencelineError

DEFAULT_ORIGINS = (
    "https://evidenceline.autopilotyourworkflow.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)
"""The planned website, plus the Vite dev server (5173) and preview server (4173)."""

logger = logging.getLogger("evidenceline.api")

MAX_QUESTION_CHARS = 500
MAX_BODY_BYTES = 64 * 1024
"""Largest request body /api/ask and /mcp read, the same cap as the website's proxy (MAX_BODY_BYTES in
web/functions/_lib/proxy.js). A question is at most 500 characters; an MCP call is a few kilobytes."""
PROXY_SECRET_HEADER = "x-evidenceline-proxy-secret"
"""Header the website's proxy (its Cloudflare Worker) sends with the shared secret, when one is set. It must
match PROXY_AUTH_HEADER in web/functions/_lib/proxy.js; a test reads that file to make sure it does."""
TRUSTED_WITHOUT_SECRET = frozenset({"x-forwarded-for"})
"""Client IP headers that can be trusted with no proxy secret: the right-most X-Forwarded-For entry is added by the
host's own load balancer, not by the caller. Any other header can be typed by whoever calls the service directly."""

TURNSTILE_TEST_SECRET = re.compile(r"[123]x0+AA")
"""Cloudflare's published Turnstile test secrets (always passes, always fails, token already spent). They are for
local testing: in production the check would pass everyone, or no one."""


@dataclass(frozen=True, slots=True)
class Settings:
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    ask_per_hour: int = 10
    """Questions one IP address may ask per rolling hour."""
    ask_per_day: int = 30
    """Questions one IP address may ask per rolling 24 hours."""
    answers_per_day: int = 300
    """Model calls across everyone per rolling 24 hours (the global spending brake)."""
    mcp_per_hour: int = 600
    """MCP requests one IP address may send per rolling hour."""
    turnstile_secret: str | None = None
    client_ip_header: str | None = None
    """Header holding the caller's IP when behind a proxy, for example 'cf-connecting-ip' or 'x-forwarded-for'
    (for X-Forwarded-For the right-most entry is used: the one the proxy itself added). None: the socket address."""
    trust_client_ip_header: bool = True
    """False when the header in client_ip_header could be forged by a direct caller: then limits count the socket
    address instead. :func:`from_env` sets it False when a header other than X-Forwarded-For is named and
    EVIDENCELINE_PROXY_SECRET is empty, and logs a warning."""
    max_question_chars: int = MAX_QUESTION_CHARS
    max_body_bytes: int = MAX_BODY_BYTES
    proxy_secret: str | None = None
    """When set, /api/ask and /mcp answer only requests carrying it in the x-evidenceline-proxy-secret header, so
    every caller comes through the website's proxy and the client IP header it sets can be trusted. /api/health
    stays open. None: any caller is served (local runs and tests)."""


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise EvidencelineError(f"{name} must be a whole number of 1 or more; got {raw!r}.")
    return int(raw)


def from_env(env: Mapping[str, str] | None = None) -> Settings:
    """Settings from ``env`` (default ``os.environ``). A malformed limit is an error at start-up, not a silent
    default."""
    source = os.environ if env is None else env
    origins = tuple(o.strip() for o in source.get("EVIDENCELINE_ALLOWED_ORIGINS", "").split(",") if o.strip())
    header = source.get("EVIDENCELINE_CLIENT_IP_HEADER", "").strip().lower() or None
    secret = source.get("EVIDENCELINE_PROXY_SECRET", "").strip() or None
    trusted = header is None or secret is not None or header in TRUSTED_WITHOUT_SECRET
    if not trusted:
        logger.warning(
            "EVIDENCELINE_CLIENT_IP_HEADER is %s but EVIDENCELINE_PROXY_SECRET is empty, so anyone calling the service "
            "directly could type that header and reset their limits. Limits count the connecting address instead "
            "until the secret is set.",
            header,
        )
    turnstile = source.get("TURNSTILE_SECRET", "").strip() or None
    if turnstile is not None and TURNSTILE_TEST_SECRET.fullmatch(turnstile):
        logger.warning(
            "TURNSTILE_SECRET is one of Cloudflare's test secrets, which pass or refuse every visitor whatever they "
            "send. Set the widget's real secret before going live."
        )
    return Settings(
        allowed_origins=origins or DEFAULT_ORIGINS,
        ask_per_hour=_int(source, "EVIDENCELINE_ASK_PER_HOUR", 10),
        ask_per_day=_int(source, "EVIDENCELINE_ASK_PER_DAY", 30),
        answers_per_day=_int(source, "EVIDENCELINE_ANSWERS_PER_DAY", 300),
        mcp_per_hour=_int(source, "EVIDENCELINE_MCP_PER_HOUR", 600),
        turnstile_secret=turnstile,
        client_ip_header=header,
        trust_client_ip_header=trusted,
        proxy_secret=secret,
    )
