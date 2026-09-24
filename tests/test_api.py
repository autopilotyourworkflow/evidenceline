"""The web API through FastAPI's TestClient, with a fake model, a fake search, a fake Turnstile and a fake clock.

The MCP mount is exercised with the MCP SDK's own Streamable HTTP client, both through an in-memory ASGI transport
and against a real uvicorn server on a local port.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import anyio
import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from evidenceline.answer import FakeClient, ModelClient
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.api.app import client_ip, create_app
from evidenceline.api.settings import DEFAULT_ORIGINS, Settings, from_env
from evidenceline.api.turnstile import CloudflareTurnstile
from evidenceline.errors import EvidencelineError
from evidenceline.guidance.models import GuidanceSearch

from .test_answer import GOOD, PASSAGES

QUESTION = "What should a detailed site investigation report include?"
SITE = "https://evidenceline.autopilotyourworkflow.com"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeTurnstile:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None]] = []

    async def verify(self, token: str, remote_ip: str | None) -> bool:
        self.seen.append((token, remote_ip))
        return token == "good-token"


@pytest.fixture(autouse=True)
def fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        return GuidanceSearch.model_construct(
            question=question, status="passages found", explanation="2 passages.", passages=PASSAGES, notes=[]
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", search)


def _app(
    settings: Settings | None = None,
    model: ModelClient | None = None,
    clock: Clock | None = None,
    turnstile: FakeTurnstile | None = None,
) -> FastAPI:
    return create_app(
        settings or Settings(),
        client_factory=lambda: model,
        clock=clock or Clock(),
        turnstile=turnstile,
        index_path=Path("no-such-index.sqlite"),
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_app(model=FakeClient(reply=GOOD))) as test_client:
        yield test_client


def _ask(test_client: TestClient, question: str = QUESTION, **extra: Any) -> httpx2.Response:
    headers = cast(dict[str, str], extra.pop("headers", {}))
    return test_client.post("/api/ask", json={"question": question, **extra}, headers=headers)


# --- endpoints -----------------------------------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["live_answers"] is True
    assert body["model"] == "fake-model"
    assert body["turnstile"] is False
    assert body["limits"]["questions_per_hour"] == 10
    assert body["limits"]["question_characters"] == 500


def test_ask_answered(client: TestClient) -> None:
    response = _ask(client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "answered"
    assert body["answer"] == GOOD
    assert body["verification"]["passed"] is True
    assert [c["number"] for c in body["citations"]] == [1, 2]


def test_ask_without_a_key_returns_passages_only() -> None:
    with TestClient(_app(model=None)) as test_client:
        assert test_client.get("/api/health").json()["live_answers"] is False
        body = _ask(test_client).json()
    assert body["status"] == "passages_only"
    assert "Live answers are switched off" in body["explanation"]
    assert len(body["citations"]) == 2


def test_guard_rail_route(client: TestClient) -> None:
    body = _ask(client, "Is this site contaminated?").json()
    assert body["status"] == "guard_rail"
    assert body["model"] is None


@pytest.mark.parametrize(
    ("payload", "words"),
    [
        ({"question": "   "}, "empty"),
        ({"question": "x" * 501}, "501 characters long; the limit is 500"),
        ({"question": "ok", "extra": 1}, "Send JSON such as"),
        ({"q": "ok"}, "Send JSON such as"),
    ],
)
def test_bad_requests(client: TestClient, payload: dict[str, Any], words: str) -> None:
    response = client.post("/api/ask", json=payload)
    assert response.status_code == 400
    assert words in response.json()["error"]
    assert "x" * 100 not in response.text  # the question is not echoed back


def test_exactly_500_characters_is_allowed(client: TestClient) -> None:
    question = ("What is a conceptual site model? " * 20)[:500]
    assert _ask(client, question).status_code == 200


# --- limits --------------------------------------------------------------------------------------------------------


def test_per_ip_hourly_limit_and_reset() -> None:
    clock = Clock()
    settings = Settings(ask_per_hour=2, ask_per_day=5)
    with TestClient(_app(settings, FakeClient(reply=GOOD), clock)) as test_client:
        assert [_ask(test_client).status_code for _ in range(2)] == [200, 200]
        refused = _ask(test_client)
        assert refused.status_code == 429
        assert "2 questions per hour" in refused.json()["error"]
        assert refused.headers["retry-after"] == "3601"
        clock.now += 3601
        assert _ask(test_client).status_code == 200


def test_per_ip_daily_limit() -> None:
    clock = Clock()
    with TestClient(_app(Settings(ask_per_hour=10, ask_per_day=3), FakeClient(reply=GOOD), clock)) as test_client:
        for _ in range(3):
            assert _ask(test_client).status_code == 200
            clock.now += 3601
        refused = _ask(test_client)
        assert refused.status_code == 429
        assert "3 questions per day" in refused.json()["error"]


def test_limits_are_per_ip_behind_a_proxy() -> None:
    settings = Settings(ask_per_hour=1, client_ip_header="x-forwarded-for")
    with TestClient(_app(settings, FakeClient(reply=GOOD))) as test_client:
        first = {"x-forwarded-for": "198.51.100.7, 203.0.113.1"}
        second = {"x-forwarded-for": "198.51.100.7, 203.0.113.2"}
        assert _ask(test_client, headers=first).status_code == 200
        assert _ask(test_client, headers=first).status_code == 429
        assert _ask(test_client, headers=second).status_code == 200


def test_global_daily_cap_pauses_live_answers() -> None:
    model = FakeClient(reply=GOOD)
    clock = Clock()
    with TestClient(_app(Settings(answers_per_day=1), model, clock)) as test_client:
        assert _ask(test_client).json()["status"] == "answered"
        paused = _ask(test_client).json()
        assert paused["status"] == "paused"
        assert "today's limit of 1 answers" in paused["explanation"]
        assert len(paused["citations"]) == 2
        # A question that needs no model is not held back by the cap.
        assert _ask(test_client, "Is this site contaminated?").json()["status"] == "guard_rail"
        clock.now += 86401
        assert _ask(test_client).json()["status"] == "answered"
    assert len(model.calls) == 2


# --- CORS ----------------------------------------------------------------------------------------------------------


def test_cors_allows_only_listed_origins(client: TestClient) -> None:
    allowed = client.options("/api/ask", headers={"Origin": SITE, "Access-Control-Request-Method": "POST"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == SITE
    other = client.options(
        "/api/ask", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert other.status_code == 400
    assert "access-control-allow-origin" not in other.headers
    simple = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert simple.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in client.get("/api/health", headers={"Origin": "https://x.y"}).headers


# --- Turnstile -----------------------------------------------------------------------------------------------------


def test_turnstile_on() -> None:
    turnstile = FakeTurnstile()
    with TestClient(_app(model=FakeClient(reply=GOOD), turnstile=turnstile)) as test_client:
        assert test_client.get("/api/health").json()["turnstile"] is True
        missing = _ask(test_client)
        assert missing.status_code == 403
        assert "not a robot" in missing.json()["error"]
        assert _ask(test_client, turnstile_token="bad-token").status_code == 403
        assert _ask(test_client, turnstile_token="good-token").status_code == 200
    assert [token for token, _ in turnstile.seen] == ["bad-token", "good-token"]


def test_turnstile_off_needs_no_token(client: TestClient) -> None:
    assert _ask(client).status_code == 200
    assert _ask(client, turnstile_token="anything").status_code == 200


def test_turnstile_from_secret_setting() -> None:
    app = _app(Settings(turnstile_secret="secret"), model=None)
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").json()["turnstile"] is True


def test_cloudflare_turnstile_client() -> None:
    posted: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        posted.append(request.content.decode())
        token = "ok" if b"response=ok" in request.content else "no"
        return httpx2.Response(200, json={"success": token == "ok"})

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    good = CloudflareTurnstile("s3cret", transport=httpx2.MockTransport(handler))
    down = CloudflareTurnstile("s3cret", transport=httpx2.MockTransport(broken))

    async def run() -> tuple[bool, bool, bool]:
        return await good.verify("ok", "203.0.113.9"), await good.verify("bad", None), await down.verify("ok", None)

    assert anyio.run(run) == (True, False, False)
    assert "secret=s3cret" in posted[0]
    assert "remoteip=203.0.113.9" in posted[0]


# --- logging -------------------------------------------------------------------------------------------------------


def test_question_text_is_never_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    secret_words = "zebra-marmalade quokka"
    with caplog.at_level(logging.DEBUG):
        _ask(client, f"What does a detailed site investigation say about {secret_words}?")
    assert "ask status=" in caplog.text
    assert secret_words not in caplog.text


# --- settings ------------------------------------------------------------------------------------------------------


def test_settings_from_env() -> None:
    assert from_env({}) == Settings()
    assert from_env({}).allowed_origins == DEFAULT_ORIGINS
    custom = from_env(
        {
            "EVIDENCELINE_ALLOWED_ORIGINS": "https://a.example, https://b.example",
            "EVIDENCELINE_ASK_PER_HOUR": "5",
            "EVIDENCELINE_ANSWERS_PER_DAY": "50",
            "TURNSTILE_SECRET": " s ",
            "EVIDENCELINE_CLIENT_IP_HEADER": "CF-Connecting-IP",
        }
    )
    assert custom.allowed_origins == ("https://a.example", "https://b.example")
    assert (custom.ask_per_hour, custom.answers_per_day, custom.turnstile_secret) == (5, 50, "s")
    assert custom.client_ip_header == "cf-connecting-ip"
    with pytest.raises(EvidencelineError, match="EVIDENCELINE_ASK_PER_DAY"):
        from_env({"EVIDENCELINE_ASK_PER_DAY": "lots"})


def test_client_ip() -> None:
    assert client_ip({}, "10.0.0.1", None) == "10.0.0.1"
    assert client_ip({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, "10.0.0.1", None) == "10.0.0.1"
    assert client_ip({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, "10.0.0.1", "x-forwarded-for") == "2.2.2.2"
    assert client_ip({"cf-connecting-ip": "3.3.3.3"}, None, "cf-connecting-ip") == "3.3.3.3"
    assert client_ip({}, None, "cf-connecting-ip") == "unknown"


# --- MCP over Streamable HTTP --------------------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "tidy_lab_files",
    "get_review_item",
    "get_results",
    "lookup_limit",
    "compare_rules",
    "check_paragraph",
    "fill_numbers",
    "search_guidelines",
    "show_redactions",
}


async def _mcp_round_trip(url: str, http: httpx2.AsyncClient | None) -> None:
    for mode in ("auto", "legacy"):
        transport = streamable_http_client(url, http_client=http)
        async with Client(transport, mode=mode) as mcp:
            listed = await mcp.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == EXPECTED_TOOLS
            for tool in tools.values():
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is True
                assert tool.input_schema.get("additionalProperties") is False
            limit = await mcp.call_tool("lookup_limit", {"analyte": "PFOS", "rule": "current"})
            assert not limit.is_error
            content = cast(dict[str, Any], limit.structured_content)
            assert (content["value"], content["unit"], content["page"]) == ("0.008", "ug/L", "49")
            wrong = await mcp.call_tool("lookup_limit", {"analyte": "PFOS", "rule": "current", "extra": 1})
            assert wrong.is_error
            tidy = await mcp.call_tool("tidy_lab_files", {})
            tidy_content = cast(dict[str, Any], tidy.structured_content)
            assert (tidy_content["rows"], tidy_content["row_count"]) == ([], 73)


def test_mcp_round_trip_in_memory() -> None:
    app = _app()

    async def run() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
                await _mcp_round_trip("http://testserver/mcp", http)
                instructions = await http.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
                    headers={"accept": "application/json, text/event-stream"},
                )  # fmt: skip
                assert "hosted, read-only copy" in instructions.text

    anyio.run(run)


def test_mcp_request_limit() -> None:
    app = _app(Settings(mcp_per_hour=1))
    with TestClient(app) as test_client:
        headers = {"accept": "application/json, text/event-stream"}
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        test_client.post("/mcp", json=body, headers=headers)
        refused = test_client.post("/mcp", json=body, headers=headers)
    assert refused.status_code == 429
    assert "1 requests per hour" in refused.json()["error"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_mcp_round_trip_over_a_real_server() -> None:
    """The same round trip against uvicorn on a local port: real HTTP, as a Claude connector would use it."""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.05)
        anyio.run(_mcp_round_trip, f"http://127.0.0.1:{port}/mcp", None)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("secret", ["1x0000000000000000000000000000000AA", "2x0000000000000000000000000000000AA"])
def test_a_cloudflare_test_secret_is_warned_about(secret: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="evidenceline.api"):
        settings = from_env({"TURNSTILE_SECRET": secret})
    assert settings.turnstile_secret == secret
    assert "test secrets" in caplog.text
    assert secret not in caplog.text


MADE_UP_WIDGET_KEY = "0x4AAAAAAA-made-up-for-tests"


def test_a_real_turnstile_secret_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="evidenceline.api"):
        from_env({"TURNSTILE_SECRET": MADE_UP_WIDGET_KEY})
    assert "test secrets" not in caplog.text


def test_the_question_limit_comes_before_turnstile() -> None:
    """A flood of bad tokens from one address stops at the limit, without a Cloudflare call for each one."""
    turnstile = FakeTurnstile()
    app = _app(Settings(ask_per_hour=2), model=FakeClient(reply=GOOD), turnstile=turnstile)
    with TestClient(app) as test_client:
        codes = [_ask(test_client, turnstile_token="bad-token").status_code for _ in range(5)]
    assert codes == [403, 403, 429, 429, 429]
    assert len(turnstile.seen) == 2
