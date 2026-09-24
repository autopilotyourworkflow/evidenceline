"""The gate in front of /api/ask and /mcp (proxy secret, then body cap), the proxy header name shared with the
website's proxy, and when a client IP header is trusted."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evidenceline.answer import FakeClient
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.api.app import create_app
from evidenceline.api.settings import MAX_BODY_BYTES, PROXY_SECRET_HEADER, Settings, from_env
from evidenceline.guidance.models import GuidanceSearch

from .test_answer import GOOD, PASSAGES

PROXY_JS = Path(__file__).resolve().parents[1] / "web" / "functions" / "_lib" / "proxy.js"
SECRET = "g" * 40
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        del k, index_path
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


@pytest.fixture
def api() -> Iterator[TestClient]:
    with TestClient(_app(Settings())) as client:
        yield client


def _js_constant(name: str) -> str:
    match = re.search(rf"export const {name} = (?:'([^']+)'|(\d+) \* (\d+))", PROXY_JS.read_text(encoding="utf-8"))
    assert match is not None, name
    if match.group(1):
        return match.group(1)
    return str(int(match.group(2)) * int(match.group(3)))


def test_secret_header_and_body_cap_match_the_website_proxy() -> None:
    assert _js_constant("PROXY_AUTH_HEADER") == PROXY_SECRET_HEADER
    assert int(_js_constant("MAX_BODY_BYTES")) == MAX_BODY_BYTES


def test_a_declared_body_over_the_cap_is_refused_before_it_is_read(api: TestClient) -> None:
    response = api.post("/api/ask", json={"question": "x" * (MAX_BODY_BYTES + 1)})
    assert response.status_code == 413
    assert response.json() == {"error": "The request is too large."}
    assert api.post("/mcp", content=b"x" * (MAX_BODY_BYTES + 1), headers=MCP_HEADERS).status_code == 413


def test_a_streamed_body_without_a_length_is_read_only_up_to_the_cap(api: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(3):
            yield b"x" * (MAX_BODY_BYTES // 2)

    response = api.post("/api/ask", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_a_body_at_the_cap_is_parsed_as_usual(api: TestClient) -> None:
    padding = " " * (MAX_BODY_BYTES - 200)
    body = f'{{"question": "What is a CSM?"{padding}}}'.encode()
    assert len(body) <= MAX_BODY_BYTES
    response = api.post("/api/ask", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert response.json()["status"] == "answered"


def test_without_the_secret_the_answer_is_403_whatever_the_size() -> None:
    with TestClient(_app(Settings(proxy_secret=SECRET))) as client:
        huge = {"question": "x" * (MAX_BODY_BYTES * 2)}
        assert client.post("/api/ask", json=huge).status_code == 403
        assert client.post("/api/ask", json=huge, headers={PROXY_SECRET_HEADER: SECRET}).status_code == 413
        small = client.post("/api/ask", json={"question": "What is a CSM?"}, headers={PROXY_SECRET_HEADER: SECRET})
        assert small.status_code == 200


def test_health_shows_the_body_cap_and_where_the_client_ip_comes_from(api: TestClient) -> None:
    body: dict[str, Any] = api.get("/api/health").json()
    assert body["limits"]["request_bytes"] == MAX_BODY_BYTES
    assert body["client_ip_from"] == "connection"


@pytest.mark.parametrize(
    ("env", "trusted"),
    [
        ({"EVIDENCELINE_CLIENT_IP_HEADER": "x-evidenceline-client-ip"}, False),
        ({"EVIDENCELINE_CLIENT_IP_HEADER": "cf-connecting-ip"}, False),
        ({"EVIDENCELINE_CLIENT_IP_HEADER": "x-forwarded-for"}, True),
        ({"EVIDENCELINE_CLIENT_IP_HEADER": "x-evidenceline-client-ip", "EVIDENCELINE_PROXY_SECRET": SECRET}, True),
        ({}, True),
    ],
    ids=["custom header, no secret", "cf header, no secret", "x-forwarded-for", "with secret", "no header"],
)
def test_client_ip_header_is_trusted_only_when_it_cannot_be_forged(env: dict[str, str], trusted: bool) -> None:
    assert from_env(env).trust_client_ip_header is trusted


def test_untrusted_header_is_logged_and_ignored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="evidenceline.api"):
        settings = from_env({"EVIDENCELINE_CLIENT_IP_HEADER": "x-evidenceline-client-ip"})
    assert "EVIDENCELINE_PROXY_SECRET is empty" in caplog.text
    with TestClient(_app(settings)) as client:
        assert client.get("/api/health").json()["client_ip_from"] == "connection"
