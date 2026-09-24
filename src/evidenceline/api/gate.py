"""The gate in front of /api/ask and /mcp: the proxy secret first, then a cap on the request body, both before any
endpoint reads or parses the body.

A direct caller without the secret gets 403 without the body being read. A body over the cap gets 413: a declared
Content-Length over it is refused at once, and a body sent without one is read only up to the cap.
"""

from __future__ import annotations

import hmac
from collections.abc import Collection, Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from evidenceline.api.settings import PROXY_SECRET_HEADER

NOT_THROUGH_PROXY = (
    "Use Evidenceline through https://evidenceline.autopilotyourworkflow.com; this address is not public."
)
TOO_LARGE = "The request is too large."


def through_proxy(headers: Mapping[str, str], secret: str | None) -> bool:
    """True when no proxy secret is configured, or the request carries it (compared in constant time)."""
    if secret is None:
        return True
    return hmac.compare_digest(headers.get(PROXY_SECRET_HEADER, "").encode(), secret.encode())


def _refusal(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers={"Cache-Control": "no-store"})


def _declared_length(headers: Mapping[str, str]) -> int | None:
    raw = headers.get("content-length", "").strip()
    return int(raw) if raw.isdigit() else None


class Gate:
    """ASGI middleware: for the listed paths, the proxy check, then the body cap; other paths pass straight on."""

    def __init__(self, app: ASGIApp, *, paths: Collection[str], secret: str | None, max_body_bytes: int) -> None:
        self._app = app
        self._paths = frozenset(paths)
        self._secret = secret
        self._max = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in self._paths:
            await self._app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not through_proxy(headers, self._secret):
            await _refusal(403, NOT_THROUGH_PROXY)(scope, receive, send)
            return
        declared = _declared_length(headers)
        if declared is not None and declared > self._max:
            await _refusal(413, TOO_LARGE)(scope, receive, send)
            return
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                await self._app(scope, _replay(bytes(body), message, receive), send)
                return
            body += message.get("body", b"")
            more = bool(message.get("more_body", False))
            if len(body) > self._max:
                await _refusal(413, TOO_LARGE)(scope, receive, send)
                return
        await self._app(scope, _replay(bytes(body), None, receive), send)


def _replay(body: bytes, pending: Message | None, receive: Receive) -> Receive:
    """A receive that gives the buffered body once, then any message already taken, then the real receive."""
    queue: list[Message] = [{"type": "http.request", "body": body, "more_body": False}]
    if pending is not None:
        queue.append(pending)

    async def replay() -> Message:
        if queue:
            return queue.pop(0)
        return await receive()

    return replay
