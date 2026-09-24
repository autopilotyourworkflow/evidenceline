"""The answer pipeline and the model client under failure, from the "Try it yourself" review of 25 September 2026:
a search that fails in an unexpected way is a plain "error" reply (never a server error, and the log never holds the
question), a reply from the model service that cannot be read is ModelFailedError, and one question always finishes
well inside the website's 60 seconds: one API call at most 25 seconds, no retries inside the SDK, and no second
attempt once 25 seconds have passed. No network, no real index."""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections import Counter
from pathlib import Path

import httpx2
import pytest

from evidenceline.answer import AnthropicClient, FakeClient, ModelFailedError, ModelPausedError, answer
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.clients import TIMEOUT, ModelReply
from evidenceline.answer.pipeline import SEARCH_FAILED, SECOND_ATTEMPT_WITHIN
from evidenceline.guidance.models import GuidanceSearch

from .test_answer import GOOD, index, searches

__all__ = ["index", "searches"]  # fixtures from test_answer, used by name below

QUESTION = "What should a detailed site investigation report include?"
UNCITED = "A detailed site investigation report includes the sampling rationale."


# --- a failed search -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [IndexError("tuple index out of range"), TypeError("int() argument"), RuntimeError("anything")]
)
def test_an_unexpected_search_failure_is_a_plain_error(
    error: Exception, index: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        raise error

    monkeypatch.setattr(pipeline_module, "search_guidelines", broken)
    secret = "zebra-marmalade quokka"
    with caplog.at_level(logging.ERROR, logger="evidenceline.answer"):
        result = answer(f"What does a DSI say about {secret}?", FakeClient(reply=GOOD), index_path=index)
    assert result.status == "error"
    assert result.explanation == SEARCH_FAILED
    assert result.model is None
    assert type(error).__name__ in caplog.text
    assert secret not in caplog.text


def test_a_failed_spelling_search_only_drops_the_offer(index: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        if question == "what is a detailed site investigation":
            raise TypeError("the second search failed")
        return GuidanceSearch.model_construct(
            question=question, status="not covered", explanation="Not covered.", passages=[], notes=[]
        )

    def words(index_path: Path | None = None) -> Counter[str]:
        del index_path
        return Counter({"detailed": 9, "investigation": 9})

    def spelt(question: str, known: Counter[str]) -> str:
        del question, known
        return "what is a detailed site investigation"

    monkeypatch.setattr(pipeline_module, "search_guidelines", search)
    monkeypatch.setattr(pipeline_module, "indexed_words", words)
    monkeypatch.setattr(pipeline_module, "corrected", spelt)
    result = answer("waht is a detialed site investigtion", FakeClient(reply=GOOD), index_path=index)
    assert result.status == "not_covered"
    assert result.did_you_mean is None


# --- time -------------------------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class SlowReplies:
    """Each call takes ``seconds`` on the clock and returns the next reply."""

    def __init__(self, clock: Clock, seconds: float, replies: list[str]) -> None:
        self.clock = clock
        self.seconds = seconds
        self.replies = replies
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "fake-slow"

    def complete(self, system: str, prompt: str) -> ModelReply:
        del system, prompt
        self.clock.now += self.seconds
        self.calls += 1
        return ModelReply(text=self.replies[self.calls - 1], model="fake-slow")


def test_no_second_attempt_once_the_question_has_taken_too_long(
    index: Path, searches: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(pipeline_module, "monotonic", clock)
    client = SlowReplies(clock, SECOND_ATTEMPT_WITHIN + 1, [UNCITED, GOOD])
    result = answer(QUESTION, client, index_path=index)
    assert client.calls == 1
    assert result.status == "passages_only"
    assert result.answer is None
    assert result.explanation.startswith("An answer was written but withheld")


def test_a_quick_failed_answer_still_gets_its_second_attempt(
    index: Path, searches: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(pipeline_module, "monotonic", clock)
    client = SlowReplies(clock, 2.0, [UNCITED, GOOD])
    result = answer(QUESTION, client, index_path=index)
    assert client.calls == 2
    assert result.status == "answered"


def test_one_api_call_has_25_seconds_and_the_worst_case_fits_in_a_minute() -> None:
    assert TIMEOUT == 25.0
    assert inspect.signature(AnthropicClient).parameters["timeout"].default == TIMEOUT
    assert SECOND_ATTEMPT_WITHIN + TIMEOUT < 60


def _anthropic(handler: httpx2.MockTransport) -> AnthropicClient:
    return AnthropicClient("sk-test", "claude-sonnet-5", transport=handler)


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}, ModelFailedError),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}, ModelFailedError),
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}, ModelPausedError),
    ],
)
def test_the_sdk_never_retries_or_waits(status: int, body: dict[str, object], error: type[Exception]) -> None:
    """One request per call: a retry, or a Retry-After of an hour, would outlast the page."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status, json=body, headers={"retry-after": "3600"})

    started = time.monotonic()
    with pytest.raises(error):
        _anthropic(httpx2.MockTransport(handler)).complete("s", "p")
    assert len(requests) == 1
    assert time.monotonic() - started < 5


# --- replies that cannot be read -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="<html><body>Gateway</body></html>", headers={"content-type": "text/html"}),
        httpx2.Response(200, json={"hello": "world"}),
        httpx2.Response(200, json=[1, 2]),
        httpx2.Response(200, content=b'{"id": "msg_1", "content": [', headers={"content-type": "application/json"}),
    ],
    ids=["html", "not a message", "a list", "cut off"],
)
def test_a_reply_that_cannot_be_read_is_model_failed(response: httpx2.Response) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return response

    with pytest.raises(ModelFailedError, match="could not be read"):
        _anthropic(httpx2.MockTransport(handler)).complete("s", "p")


def test_an_unreadable_reply_becomes_an_error_result(index: Path, searches: list[str]) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"hello": "world"})

    result = answer(QUESTION, _anthropic(httpx2.MockTransport(handler)), index_path=index)
    assert result.status == "error"
    assert "could not be read" in result.explanation
    assert json.loads(result.model_dump_json())["answer"] is None
