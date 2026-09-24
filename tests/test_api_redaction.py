"""The hosted demo's redaction: with EVIDENCELINE_REDACT=builtin:fds01-demo (as render.yaml sets it), the fictional
FDS-01 client name and site address never come back from /mcp or /api/ask, in any spelling tried here.

Every identifier used here is fictional: it is the made-up client and address in the header of
``src/evidenceline/data/fds01_site/field_sheet.csv``. The model is a fake and the search is a fake.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from evidenceline import redact
from evidenceline.answer import FakeClient
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.api.app import QUESTION_REDACTED, create_app, hosted_note, load_identifiers
from evidenceline.api.settings import Settings
from evidenceline.guidance.models import GuidanceSearch
from evidenceline.redact import RedactionConfig

from .test_answer import GOOD, PASSAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
FIELD_SHEET = REPO_ROOT / "src" / "evidenceline" / "data" / "fds01_site" / "field_sheet.csv"
DEMO = "builtin:fds01-demo"
DASHES = ("\u2013", "\u2014")

CLIENT_SPELLINGS = (
    "Harbourline Logistics",
    "HARBOURLINE LOGISTICS",
    "harbourline-logistics",
    "Harbourline_Logistics",
    "Harbourline\nLogistics",
    "Harbourline\u00a0Logistics",
    "Harbour\u00adline Logistics",
    "HarbourlineLogistics",
    "harbourlinelogistics.com.au",
    "Harbourline Logistics Pty Ltd",
    "Harbourline Logistics Pty. Ltd.",
    "Harbour Line Logistics",
    "Harbourline",
)
ADDRESS_SPELLINGS = (
    "12 Example Road, Welshpool WA",
    "12 EXAMPLE ROAD WELSHPOOL WA 6106",
    "12 Example Rd, Welshpool",
    "12 example road",
    "Example Road, Welshpool",
    "Welshpool WA 6106, 12 Example Road",
)
FORBIDDEN = ("harbourline", "harbour line", "example road", "example rd", "welshpool")
"""Case-folded fragments that must not appear anywhere in a response. Soft hyphens and no-break spaces are removed
or folded before the check, so a spelling that survives with one of them inside is still caught."""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "").replace("\\u00ad", "").replace("\\n", " ")).casefold()


def _assert_clean(*outputs: str) -> None:
    for output in outputs:
        folded = _clean(output)
        for fragment in FORBIDDEN:
            assert fragment not in folded, (fragment, output[:300])


def _field_sheet_identifiers() -> list[str]:
    text = FIELD_SHEET.read_text(encoding="utf-8")
    return re.findall(r"^# (?:Client|Site address): (.+?)$", text, flags=re.MULTILINE)


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, DEMO)
    redact.reset_session()


@pytest.fixture(autouse=True)
def searches(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        seen.append(question)
        return GuidanceSearch.model_construct(
            question=question, status="passages found", explanation="2 passages.", passages=PASSAGES, notes=[]
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", search)
    return seen


def _app(model: FakeClient | None = None) -> FastAPI:
    return create_app(Settings(), client_factory=lambda: model, index_path=Path("no-such-index.sqlite"))


def _over_http[T](app: FastAPI, body: Callable[[Client], Awaitable[T]]) -> T:
    async def run() -> T:
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            http = httpx2.AsyncClient(transport=transport, base_url="http://testserver", timeout=120)
            async with http, Client(streamable_http_client("http://testserver/mcp", http_client=http)) as client:
                return await body(client)

    return anyio.run(run)


def test_the_field_sheet_header_is_what_the_demo_file_lists() -> None:
    """The demo file must cover the header exactly as written, so a change to either is caught here."""
    headers = _field_sheet_identifiers()
    assert len(headers) == 2
    redactor = redact.Redactor(redact.load_builtin("fds01-demo"), None)
    for line in headers:
        _assert_clean(redactor.redact_text(line))


# --- /mcp ----------------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("demo")
def test_mcp_never_returns_the_fictional_client_or_address() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    for name in (*CLIENT_SPELLINGS, *ADDRESS_SPELLINGS):
        calls += [
            ("check_paragraph", {"text": f"At {name}, PFOS in MB2 was 0.038 ug/L in September 2025.", "well": "MB2"}),
            ("lookup_limit", {"analyte": name, "rule": "current"}),  # an error that quotes the argument
            ("get_results", {"well": f"{name} MB2"}),
        ]
    calls += [
        ("fill_numbers", {"text": "Harbourline Logistics: PFOS was {PFOS|MB2|Sep 2025}.", "well": "MB2"}),
        ("get_results", {"well": "MB2", "Harbourline_Logistics": 1}),  # an unknown argument name
        ("tidy_lab_files", {"site": "12 Example Road, Welshpool WA"}),
        ("show_redactions", {}),
    ]

    async def body(client: Client) -> tuple[str, list[CallToolResult]]:
        return client.instructions or "", [await client.call_tool(name, args) for name, args in calls]

    instructions, results = _over_http(_app(), body)
    _assert_clean(*(result.model_dump_json() for result in results))
    first = results[0].model_dump_json()
    assert "[CLIENT-1]" in first
    assert "the built-in demo identifier file" in instructions
    assert "fictional client name and site address" in instructions
    assert "NOT redacted" in instructions
    assert not any(dash in instructions for dash in DASHES)
    summary = results[-1].model_dump_json()
    assert "Built-in identifier file loaded (builtin:fds01-demo)" in summary
    assert '"CLIENT":1' in summary.replace(" ", "")


def test_mcp_fails_closed_when_the_named_file_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "missing.toml"))
    redact.reset_session()

    async def body(client: Client) -> tuple[str, CallToolResult]:
        paragraph = "Harbourline Logistics asked about MB2."
        return client.instructions or "", await client.call_tool("check_paragraph", {"text": paragraph, "well": "MB2"})

    instructions, result = _over_http(_app(), body)
    assert result.is_error
    _assert_clean(result.model_dump_json())
    assert "could not be read" in instructions
    assert "become placeholders" not in instructions


@pytest.mark.parametrize("value", ["builtin:no-such-file", "builtin:"])
def test_an_unknown_built_in_file_fails_closed(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, value)
    redact.reset_session()

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("lookup_limit", {"analyte": "PFOS", "rule": "current"})

    result = _over_http(_app(), body)
    assert result.is_error
    assert "builtin:fds01-demo" in result.model_dump_json()


# --- /api/ask ------------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("demo")
@pytest.mark.parametrize("name", [*CLIENT_SPELLINGS, *ADDRESS_SPELLINGS])
def test_ask_never_returns_the_fictional_client_or_address(name: str, searches: list[str]) -> None:
    model = FakeClient(reply=GOOD)
    question = f"What goes in a detailed site investigation report for {name}?"
    with TestClient(_app(model)) as client:
        response = client.post("/api/ask", json={"question": question})
    assert response.status_code == 200
    body = response.json()
    _assert_clean(response.text, searches[0], *(prompt for _, prompt in model.calls))
    assert body["question_redactions"] >= 1
    notes = [note for note in body["notes"] if note.endswith(QUESTION_REDACTED)]
    assert notes == [f"{body['question_redactions']}{QUESTION_REDACTED}"]


@pytest.mark.usefixtures("demo")
def test_ask_counts_listed_and_shape_found_names_together() -> None:
    """Listed names and a name found by shape are numbered in one pass (never two names as [CLIENT-1]), with one
    note and one total."""
    model = FakeClient(reply=GOOD)
    question = "Harbourline at 12 Example Rd, Welshpool, and my client Redgum: what goes in a DSI report?"
    with TestClient(_app(model)) as client:
        body = client.post("/api/ask", json={"question": question}).json()
    assert body["question_redactions"] == 3
    assert body["question"] == "[CLIENT-1] at [ADDRESS-1], and my client [CLIENT-2]: what goes in a DSI report?"
    assert body["question"] in model.calls[0][1]
    assert [note for note in body["notes"] if note.endswith(QUESTION_REDACTED)] == [f"3{QUESTION_REDACTED}"]


def test_ask_is_refused_when_the_named_file_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "missing.toml"))
    model = FakeClient(reply=GOOD)
    with TestClient(_app(model)) as client:
        response = client.post("/api/ask", json={"question": "Harbourline Logistics: what is a DSI?"})
    assert response.status_code == 503
    assert "redaction settings could not be loaded" in response.json()["error"]
    _assert_clean(response.text)
    assert model.calls == []


def test_ask_without_an_identifier_file_is_unchanged(searches: list[str]) -> None:
    """No identifier file (the default): the pipeline's own name search is all there is, as before."""
    model = FakeClient(reply=GOOD)
    with TestClient(_app(model)) as client:
        body = client.post("/api/ask", json={"question": "Is Harbourline Logistics Pty Ltd's DSI complete?"}).json()
    assert body["question_redactions"] == 1
    assert "Harbourline" not in searches[0]


# --- the hosted note and the deployment setting --------------------------------------------------------------------


def test_hosted_note_promises_only_what_the_config_can_do() -> None:
    none = hosted_note(RedactionConfig((), "built-in patterns only"))
    assert "not redacted" in none
    assert "[CLIENT-1]" not in none
    demo = hosted_note(redact.load_builtin("fds01-demo"))
    assert "[CLIENT-1]" in demo
    assert "NOT redacted" in demo
    own = hosted_note(RedactionConfig((redact.Identity("CLIENT", ("Example Holdings",)),), "loaded"))
    assert "Names it does not list are NOT redacted" in own
    broken = hosted_note(None)
    assert "returns an error" in broken
    for note in (none, demo, own, broken):
        assert "hosted, read-only copy" in note
        assert not any(dash in note for dash in DASHES)


def test_load_identifiers_is_none_only_for_a_broken_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_identifiers() is not None
    monkeypatch.setenv(redact.CONFIG_ENV, DEMO)
    demo = load_identifiers()
    assert demo is not None
    assert demo.builtin == "fds01-demo"
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "missing.toml"))
    assert load_identifiers() is None


def test_health_says_which_identifiers_are_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(_app()) as client:
        assert "built-in patterns only" in client.get("/api/health").json()["redaction"]
    monkeypatch.setenv(redact.CONFIG_ENV, DEMO)
    with TestClient(_app()) as client:
        assert client.get("/api/health").json()["redaction"] == "Built-in identifier file loaded (builtin:fds01-demo)."
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "Client Acme.toml"))
    with TestClient(_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["redaction"] == "The identifier file could not be loaded."
    assert "Acme" not in response.text


def test_render_blueprint_selects_the_demo_identifier_file() -> None:
    blueprint = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
    assert re.search(r"- key: EVIDENCELINE_REDACT\n\s+value: builtin:fds01-demo\n", blueprint)
