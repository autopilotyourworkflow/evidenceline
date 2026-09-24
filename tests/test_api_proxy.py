"""The web API behind the website's proxy: the optional shared secret and the client IP header it vouches for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evidenceline.answer import FakeClient
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.api.app import create_app, through_proxy
from evidenceline.api.settings import PROXY_SECRET_HEADER, Settings, from_env
from evidenceline.guidance.models import GuidanceSearch

from .test_answer import GOOD, PASSAGES

SECRET = "s3cret-value"
MCP_HEADERS = {"accept": "application/json, text/event-stream"}
MCP_BODY: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.fixture(autouse=True)
def fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        return GuidanceSearch.model_construct(
            question=question, status="passages found", explanation="2 passages.", passages=PASSAGES, notes=[]
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", search)


def _app(settings: Settings) -> FastAPI:
    return create_app(
        settings,
        client_factory=lambda: FakeClient(reply=GOOD),
        clock=lambda: 1000.0,
        index_path=Path("no-such-index.sqlite"),
    )


def test_secret_read_from_env() -> None:
    assert from_env({"EVIDENCELINE_PROXY_SECRET": f"  {SECRET} "}).proxy_secret == SECRET
    assert from_env({}).proxy_secret is None


def test_through_proxy() -> None:
    assert through_proxy({}, None)
    assert through_proxy({PROXY_SECRET_HEADER: SECRET}, SECRET)
    assert not through_proxy({}, SECRET)
    assert not through_proxy({PROXY_SECRET_HEADER: "wrong"}, SECRET)


def test_without_the_secret_ask_and_mcp_are_refused_but_health_is_open() -> None:
    with TestClient(_app(Settings(proxy_secret=SECRET))) as client:
        health = client.get("/api/health")
        ask = client.post("/api/ask", json={"question": "What is a conceptual site model?"})
        mcp = client.post("/mcp", json=MCP_BODY, headers=MCP_HEADERS)
    assert health.status_code == 200
    assert health.json()["proxy_only"] is True
    assert ask.status_code == 403
    assert "evidenceline.autopilotyourworkflow.com" in ask.json()["error"]
    assert mcp.status_code == 403


def test_with_the_secret_both_are_served() -> None:
    with TestClient(_app(Settings(proxy_secret=SECRET))) as client:
        ask = client.post(
            "/api/ask", json={"question": "What is a conceptual site model?"}, headers={PROXY_SECRET_HEADER: SECRET}
        )
        mcp = client.post("/mcp", json=MCP_BODY, headers={**MCP_HEADERS, PROXY_SECRET_HEADER: SECRET})
    assert ask.status_code == 200
    assert mcp.status_code == 200


def test_limits_follow_the_client_ip_header_the_proxy_sets() -> None:
    settings = Settings(proxy_secret=SECRET, client_ip_header="x-evidenceline-client-ip", ask_per_hour=1)
    with TestClient(_app(settings)) as client:

        def ask(ip: str) -> int:
            headers = {PROXY_SECRET_HEADER: SECRET, "x-evidenceline-client-ip": ip}
            return client.post("/api/ask", json={"question": "What is a CSM?"}, headers=headers).status_code

        assert [ask("203.0.113.1"), ask("203.0.113.2"), ask("203.0.113.1")] == [200, 200, 429]
