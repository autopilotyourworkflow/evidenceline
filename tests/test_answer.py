"""The answer pipeline end to end with a fake model and a fake search, and the model clients without a network.

The search is replaced so these tests do not depend on the local guidance index; one test at the end runs the real
index when it has been built.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import httpx2
import pytest

from evidenceline.answer import (
    AnthropicClient,
    ClaudeCliClient,
    FakeClient,
    ModelFailedError,
    ModelPausedError,
    answer,
)
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.context import passage_texts
from evidenceline.answer.prompt import SYSTEM
from evidenceline.errors import EvidencelineError
from evidenceline.guidance.models import GuidanceSearch, Passage
from evidenceline.guidance.search import default_index_path

DSI_TEXT = (
    "9. Detailed site investigation. A detailed site investigation report includes the sampling rationale, "
    "the results for 12 wells and the conceptual site model, and is submitted within 21 days."
)
REPORT_TEXT = "Known or suspected contaminated sites must be reported to DWER using the prescribed form."
GOOD = "A detailed site investigation report includes the sampling rationale and results [1]. It is reported [2]."


def _passage(rank: int, doc: str, page: int, section: str, excerpt: str) -> Passage:
    fields: dict[str, Any] = {
        "rank": rank,
        "document_id": doc,
        "document_title": f"Title of {doc}",
        "edition": "November 2021",
        "publication_date": "2021-11",
        "wa_status": "WA guideline.",
        "pdf_page": page,
        "printed_page": str(page - 5),
        "printed_page_basis": "printed on the page",
        "section": section,
        "section_path": section,
        "location": f"p. {page - 5} (PDF p. {page}), {section}",
        "excerpt": excerpt,
        "excerpt_words": len(excerpt.split()),
        "licence_lane": "B",
        "licence": "WA Government",
        "notice": "Notice.",
        "official_url": f"https://example.org/{doc}.pdf",
        "link": f"https://example.org/{doc}.pdf#page={page}",
        "matched": [],
        "coverage": "1.00",
        "note": "Note.",
    }
    known = set(Passage.model_fields)
    return Passage.model_construct(**{k: v for k, v in fields.items() if k in known})


PASSAGES = [
    _passage(1, "dwer-amcs", 34, "9. Detailed site investigation", "A detailed site investigation report includes"),
    _passage(2, "dwer-irc", 30, "6.3.1 Prescribed form", "reported to DWER using the prescribed form"),
]


@pytest.fixture
def index(tmp_path: Path) -> Path:
    """A tiny SQLite file with the chunks table the pipeline reads page text from."""
    path = tmp_path / "guidance.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id TEXT, pdf_page INTEGER, section TEXT, text TEXT)")
    db.executemany(
        "INSERT INTO chunks(doc_id, pdf_page, section, text) VALUES (?, ?, ?, ?)",
        [
            ("dwer-amcs", 34, "9. Detailed site investigation", DSI_TEXT),
            ("dwer-irc", 30, "6.3.1 Prescribed form", REPORT_TEXT),
            ("dwer-irc", 30, "Other heading", "Unrelated text on the same page, 99 of it."),
        ],
    )
    db.commit()
    db.close()
    return path


@pytest.fixture
def searches(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the search with one that finds PASSAGES, or nothing for questions about NSW. Records questions."""
    seen: list[str] = []

    def fake_search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        seen.append(question)
        assert k == 8
        assert index_path is not None
        if "NSW" in question:
            return GuidanceSearch.model_construct(
                question=question,
                status="not covered",
                explanation="The indexed guidelines don't appear to cover this: the question is about NSW rules.",
                passages=[],
                notes=[],
            )
        return GuidanceSearch.model_construct(
            question=question,
            status="passages found",
            explanation="2 passages.",
            passages=PASSAGES,
            notes=["Each passage shows its document's edition and WA status."],
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", fake_search)
    return seen


# --- every status --------------------------------------------------------------------------------------------------


def test_answered(index: Path, searches: list[str]) -> None:
    client = FakeClient(reply=GOOD)
    result = answer("What should a detailed site investigation report include?", client, index_path=index)
    assert result.status == "answered"
    assert result.answer == GOOD
    assert result.model == "fake-model"
    assert result.verification.passed is True
    assert [(c.number, c.cited) for c in result.citations] == [(1, True), (2, True)]
    assert result.citations[0].link == "https://example.org/dwer-amcs.pdf#page=34"
    system, prompt = client.calls[0]
    assert system == SYSTEM
    assert DSI_TEXT in prompt  # the whole page, not only the excerpt
    assert REPORT_TEXT in prompt  # the chunk in the passage's own section, not the other one on that page
    assert "99 of it" not in prompt
    assert "<guideline_values>" not in prompt
    assert searches == ["What should a detailed site investigation report include?"]


def test_answer_with_values_gets_both_rules(index: Path, searches: list[str]) -> None:
    reply = (
        "Under PFAS NEMP 3.0, PFOS and PFHxS are added and compared with 0.07 ug/L [G1]. Under the current ADWG "
        "values, PFOS alone is compared with 8 ng/L [G2]."
    )
    client = FakeClient(reply=reply)
    result = answer("What is the drinking-water limit for PFOS?", client, index_path=index)
    assert result.status == "answered"
    assert [(v.rule, v.value) for v in result.guideline_values] == [("nemp-3.0", "0.07"), ("current", "0.008")]
    prompt = client.calls[0][1]
    # Same compared quantity as lookup_limit (footnote a); the note still says the rule sets it for the sum.
    assert "[G1] PFAS NEMP 3.0 drinking-water values: PFOS on its own 0.07 ug/L" in prompt
    assert "the value for the sum of PFOS and PFHxS, 0.07 ug/L, also applies to PFOS on its own" in prompt
    assert "[G2] Current ADWG drinking-water values" in prompt
    assert any("never from the passages" in note for note in result.notes)
    assert not any("lookup_limit" in note for note in result.notes)


def test_passages_only_when_live_answers_are_off(index: Path, searches: list[str]) -> None:
    result = answer("What should a detailed site investigation report include?", None, index_path=index)
    assert result.status == "passages_only"
    assert result.answer is None
    assert result.model is None
    assert "switched off" in result.explanation
    assert result.verification.ran is False
    assert len(result.citations) == 2


def test_passages_only_when_the_answer_fails_a_check(index: Path, searches: list[str]) -> None:
    client = FakeClient(reply="The report covers 14 wells [1]. It is reported [2].")
    result = answer("What should a detailed site investigation report include?", client, index_path=index)
    assert result.status == "passages_only"
    assert result.answer is None
    assert result.verification.passed is False
    assert "14" in result.explanation
    assert "withheld" in result.explanation
    assert [c.cited for c in result.citations] == [False, False]


def test_not_covered_by_the_search_calls_no_model(index: Path, searches: list[str]) -> None:
    client = FakeClient(reply=GOOD)
    result = answer("What are the NSW rules for PFAS in soil?", client, index_path=index)
    assert result.status == "not_covered"
    assert client.calls == []
    assert "NSW" in result.explanation
    assert result.citations == []


def test_not_covered_by_the_model(index: Path, searches: list[str]) -> None:
    client = FakeClient(reply="NOT_COVERED")
    result = answer("What should a detailed site investigation report include?", client, index_path=index)
    assert result.status == "not_covered"
    assert result.answer is None
    assert result.model == "fake-model"
    assert len(result.citations) == 2


def test_guard_rail_calls_no_model(index: Path, searches: list[str]) -> None:
    client = FakeClient(reply=GOOD)
    result = answer("Is this site contaminated?", client, index_path=index)
    assert result.status == "guard_rail"
    assert client.calls == []
    assert result.answer is not None
    assert result.answer.startswith("Evidenceline does not decide whether a site is contaminated")
    assert "investigation level" in result.answer
    assert len(result.citations) == 2
    assert result.model is None


def test_paused(index: Path, searches: list[str]) -> None:
    client = FakeClient(error=ModelPausedError("Live answers are paused: their spending cap has been reached."))
    result = answer("What should a detailed site investigation report include?", client, index_path=index)
    assert result.status == "paused"
    assert "spending cap" in result.explanation
    assert len(result.citations) == 2


def test_model_error(index: Path, searches: list[str]) -> None:
    client = FakeClient(error=ModelFailedError("The model service could not be reached."))
    result = answer("What should a detailed site investigation report include?", client, index_path=index)
    assert result.status == "error"
    assert "could not be reached" in result.explanation


def test_search_error(monkeypatch: pytest.MonkeyPatch, index: Path) -> None:
    def broken(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        raise EvidencelineError("The guidance index has not been built.")

    monkeypatch.setattr(pipeline_module, "search_guidelines", broken)
    result = answer("What is a conceptual site model?", FakeClient(reply=GOOD), index_path=index)
    assert result.status == "error"
    assert "has not been built" in result.explanation


# --- redaction -----------------------------------------------------------------------------------------------------


def test_question_is_redacted_before_search_and_model(index: Path, searches: list[str]) -> None:
    client = FakeClient(reply=GOOD)
    question = (
        "Our client jo.bloggs@example.com at 12 Example Road, Welshpool WA 6106 (Lot 123) asks what a detailed "
        "site investigation report should include?"
    )
    result = answer(question, client, index_path=index)
    for raw in ("jo.bloggs@example.com", "Example Road", "Lot 123"):
        assert raw not in searches[0]
        assert raw not in client.calls[0][1]
        assert raw not in result.model_dump_json()
    assert "[EMAIL-1]" in result.question
    assert "[ADDRESS-1]" in result.question
    assert result.question_redactions == 3
    assert any("3 identifier(s)" in note for note in result.notes)


# --- page text for the model ---------------------------------------------------------------------------------------


def test_page_text_falls_back_to_the_excerpt(tmp_path: Path) -> None:
    missing = [_passage(1, "nowhere", 1, "x", "only the excerpt")]
    texts = passage_texts(missing, tmp_path / "no-such-index.sqlite")
    assert texts[0].body == "only the excerpt"
    assert texts[0].header.startswith("[1] Title of nowhere")


# --- clients -------------------------------------------------------------------------------------------------------


def _cli_result(payload: dict[str, Any], code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=json.dumps(payload), stderr="")


def test_cli_client_runs_headless_with_no_tools() -> None:
    seen: list[tuple[list[str], str, Path]] = []

    def runner(command: list[str], stdin: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        seen.append((command, stdin, cwd))
        return _cli_result(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "stop_reason": "end_turn",
                "result": " An answer [1]. ",
                "modelUsage": {"claude-haiku-4-5-20251001": {}, "claude-sonnet-5": {}},
            }
        )

    client = ClaudeCliClient(executable=Path("claude.exe"), runner=runner)
    reply = client.complete("system text", "user prompt")
    assert (reply.text, reply.model) == ("An answer [1].", "claude-sonnet-5")
    command, stdin, cwd = seen[0]
    assert command[:2] == ["claude.exe", "-p"]
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--system-prompt") + 1] == "system text"
    assert "--safe-mode" in command
    assert stdin == "user prompt"
    assert cwd.name.startswith("evidenceline-cli-")


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"subtype": "error_during_execution", "is_error": True}, ModelFailedError),
        ({"subtype": "success", "is_error": True, "api_error_status": 429}, ModelPausedError),
        ({"subtype": "success", "is_error": False, "stop_reason": "refusal", "result": ""}, ModelFailedError),
    ],
)
def test_cli_client_errors(payload: dict[str, Any], error: type[Exception]) -> None:
    client = ClaudeCliClient(executable=Path("claude.exe"), runner=lambda c, s, w: _cli_result(payload))
    with pytest.raises(error):
        client.complete("s", "p")


def test_cli_client_bad_output() -> None:
    def runner(command: list[str], stdin: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="not json", stderr="")

    with pytest.raises(ModelFailedError, match="did not return JSON"):
        ClaudeCliClient(executable=Path("claude.exe"), runner=runner).complete("s", "p")


def _anthropic(handler: Any) -> AnthropicClient:
    return AnthropicClient("sk-test", "claude-sonnet-5", transport=httpx2.MockTransport(handler))


def _error_response(status: int, kind: str, message: str) -> httpx2.Response:
    return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": message}})


def test_anthropic_client_answer() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "An answer [1]."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    reply = _anthropic(handler).complete("system text", "user prompt")
    assert (reply.text, reply.model) == ("An answer [1].", "claude-sonnet-5")
    body = sent[0]
    assert body["model"] == "claude-sonnet-5"
    assert body["system"] == "system text"
    assert body["messages"] == [{"role": "user", "content": "user prompt"}]
    assert "tools" not in body


@pytest.mark.parametrize(
    ("response", "error", "words"),
    [
        (_error_response(402, "billing_error", "Payment required"), ModelPausedError, "spending cap"),
        (
            _error_response(400, "invalid_request_error", "You have reached your specified API usage limits."),
            ModelPausedError,
            "spending cap",
        ),
        (_error_response(429, "rate_limit_error", "Slow down"), ModelPausedError, "rate limit"),
        (_error_response(401, "authentication_error", "bad key"), ModelFailedError, "API key"),
        (_error_response(400, "invalid_request_error", "bad request"), ModelFailedError, "HTTP 400"),
    ],
)
def test_anthropic_client_errors(response: httpx2.Response, error: type[Exception], words: str) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return response

    with pytest.raises(error, match=words):
        _anthropic(handler).complete("s", "p")


def test_anthropic_client_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AnthropicClient.from_env() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("EVIDENCELINE_MODEL", raising=False)
    client = AnthropicClient.from_env()
    assert client is not None
    assert client.model_id == "claude-sonnet-5"
    monkeypatch.setenv("EVIDENCELINE_MODEL", "claude-opus-5")
    other = AnthropicClient.from_env()
    assert other is not None
    assert other.model_id == "claude-opus-5"


# --- the real index, when it has been built ------------------------------------------------------------------------


@pytest.mark.skipif(not default_index_path().exists(), reason="guidance index not built")
def test_real_index_guard_rail_and_passages() -> None:
    client = FakeClient(reply="A conceptual site model describes sources, pathways and receptors [1].")
    guarded = answer("Is this site contaminated?", client)
    assert guarded.status in {"guard_rail", "not_covered"}
    assert client.calls == []
    off = answer("What is a conceptual site model?", None)
    assert off.status in {"passages_only", "not_covered"}
    for citation in off.citations:
        assert citation.link.startswith("http")
        assert "\u2013" not in citation.excerpt


def test_cli_path_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from evidenceline.answer.clients import default_cli

    monkeypatch.setenv("EVIDENCELINE_CLAUDE_CLI", str(tmp_path / "claude.exe"))
    assert default_cli() == tmp_path / "claude.exe"
    assert ClaudeCliClient().executable == tmp_path / "claude.exe"
