"""Optional Cloudflare Turnstile check: on only when ``TURNSTILE_SECRET`` is set.

The website sends the widget's token with the question; the server asks Cloudflare's siteverify endpoint whether
it is valid. Any failure to reach Cloudflare counts as "not verified": the check fails closed.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

import httpx2

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerifier(Protocol):
    async def verify(self, token: str, remote_ip: str | None) -> bool: ...


class CloudflareTurnstile:
    """Calls Cloudflare's siteverify endpoint with the secret, the token and the caller's IP."""

    def __init__(self, secret: str, *, timeout: float = 5.0, transport: httpx2.AsyncBaseTransport | None = None):
        self._secret = secret
        self._timeout = timeout
        self._transport = transport

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        form = {"secret": self._secret, "response": token}
        if remote_ip:
            form["remoteip"] = remote_ip
        try:
            async with httpx2.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(SITEVERIFY_URL, data=form)
            payload: object = response.json()
        except (httpx2.HTTPError, ValueError):
            return False
        # Cloudflare answers with a JSON object; anything else (a list, null, a string) counts as not verified.
        if response.status_code != 200 or not isinstance(payload, dict):
            return False
        return cast(dict[str, Any], payload).get("success") is True
