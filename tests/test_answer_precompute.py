"""The answers prepared in advance (web/public/data/answers.json) and the script that writes them."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from evidenceline.answer import AnswerResult, FakeClient
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.clients import DEFAULT_MODEL
from evidenceline.answer.prompt import SYSTEM
from evidenceline.guidance.models import GuidanceSearch

from .test_answer import GOOD, PASSAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSWERS = REPO_ROOT / "web" / "public" / "data" / "answers.json"
DASHES = ("\u2013", "\u2014")


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("precompute_answers", REPO_ROOT / "scripts" / "precompute_answers.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        return GuidanceSearch.model_construct(
            question=question, status="passages found", explanation="2 passages.", passages=PASSAGES, notes=[]
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", search)


def test_build_labels_every_entry(fake_search: None) -> None:
    script = _script()
    day = dt.date(2026, 9, 24)
    data = script.build(FakeClient(reply=GOOD, model="claude-sonnet-5"), ["What is a conceptual site model?"], day)
    entry = data["answers"][0]
    assert entry["result"]["status"] == "answered"
    assert entry["prepared_on"] == "2026-09-24"
    assert entry["label"] == (
        "Prepared in advance on 24 September 2026 by the same pipeline as the live question box, with the model "
        "claude-sonnet-5. The answer passed every check before it was saved."
    )
    assert data["model"] == "claude-sonnet-5"
    assert script.check(data) == []


def test_withheld_answer_is_labelled_and_never_shown(fake_search: None) -> None:
    script = _script()
    data = script.build(
        FakeClient(reply="It covers 14 wells [1]."), ["What is a conceptual site model?"], dt.date.today()
    )
    entry = data["answers"][0]
    assert entry["result"]["status"] == "passages_only"
    assert entry["result"]["answer"] is None
    assert "did not pass the checks, so it was withheld" in entry["label"]
    assert entry["result"]["verification"]["passed"] is False


def test_check_catches_dashes_and_unchecked_answers(fake_search: None) -> None:
    script = _script()
    data = script.build(FakeClient(reply=GOOD), ["What is a conceptual site model?"], dt.date.today())
    data["answers"][0]["label"] += " \u2014 oops"
    data["answers"][0]["result"]["verification"]["passed"] = False
    problems = script.check(data)
    assert len(problems) == 2


@pytest.mark.skipif(not ANSWERS.exists(), reason="answers.json not prepared yet")
def test_prepared_answers_file() -> None:
    data = json.loads(ANSWERS.read_text(encoding="utf-8"))
    script = _script()
    assert [entry["question"] for entry in data["answers"]] == list(script.QUESTIONS)
    assert data["model"]
    assert "same pipeline" in data["label"]
    if "system_prompt_sha256" in data:  # recorded from the --model change on; older files predate it
        assert data["system_prompt_sha256"] == script.prompt_fingerprint(), "answers.json predates the prompt"
    # Every other suggested question is prepared too, for lookup only (no button), so a click needs no live call.
    lookups = [entry["question"] for entry in data.get("lookup_only", [])]
    assert lookups == list(script.LOOKUP_QUESTIONS), "answers.json predates the lookup-only entries"
    assert script.check(data) == []
    for entry in [*data["answers"], *data.get("lookup_only", [])]:
        result = AnswerResult.model_validate(entry["result"])
        assert entry["label"].startswith("Prepared in advance on ")
        assert "same pipeline as the live question box" in entry["label"]
        if result.status == "answered":
            assert result.verification.passed is True
            assert result.answer is not None
            assert result.model is not None
            assert result.model in entry["label"]
            assert any(citation.cited for citation in result.citations)
        else:
            assert result.answer is None or result.status == "guard_rail"
        for text in (entry["label"], result.explanation, result.answer or "", *result.notes):
            assert not any(dash in text for dash in DASHES)
    statuses = {entry["question"]: entry["result"]["status"] for entry in data["answers"]}
    assert statuses["Is this site contaminated?"] == "guard_rail"
    assert statuses["What are the NSW rules for PFAS in soil?"] == "not_covered"


def test_every_other_suggested_question_is_prepared_for_lookup_only() -> None:
    script = _script()
    pool = json.loads((REPO_ROOT / "src" / "evidenceline" / "data" / "suggested_questions.json").read_text("utf-8"))
    suggested = [str(item["question"]) for item in pool["questions"]]
    assert list(script.LOOKUP_QUESTIONS) == [q for q in suggested if q not in script.QUESTIONS]
    assert not set(script.QUESTIONS) & set(script.LOOKUP_QUESTIONS)


def test_build_puts_lookup_entries_after_the_buttons_and_checks_them(fake_search: None) -> None:
    script = _script()
    data = script.build(
        FakeClient(reply=GOOD), ["What is a conceptual site model?"], dt.date(2026, 9, 25), lookups=["What is PFAS?"]
    )
    assert [e["question"] for e in data["answers"]] == ["What is a conceptual site model?"]
    assert [e["question"] for e in data["lookup_only"]] == ["What is PFAS?"]
    assert data["lookup_only"][0]["result"]["status"] == "answered"
    assert data["lookup_only"][0]["label"].startswith("Prepared in advance on 25 September 2026")
    assert script.check(data) == []
    data["lookup_only"][0]["label"] += f" {DASHES[1]} oops"
    assert len(script.check(data)) == 1


def test_build_records_the_model_and_the_prompt(fake_search: None) -> None:
    script = _script()
    data = script.build(
        FakeClient(reply=GOOD, model="claude-opus-5"), ["What is a conceptual site model?"], dt.date.today()
    )
    assert data["model"] == "claude-opus-5"
    assert data["models_reported"] == ["claude-opus-5"]
    assert data["system_prompt_sha256"] == hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()


def test_no_model_call_means_no_model_reported(fake_search: None) -> None:
    script = _script()
    data = script.build(FakeClient(reply=GOOD), ["Is this site contaminated?"], dt.date.today())
    assert data["models_reported"] == []


def test_model_argument_defaults_and_explicit() -> None:
    script = _script()
    assert script.parse_args([]).model == DEFAULT_MODEL
    assert script.parse_args(["--model", "claude-opus-5-5[1m]"]).model == "claude-opus-5-5[1m]"
    assert script.parse_args(["--model=sonnet"]).model == "sonnet"


@pytest.mark.parametrize("argv", [["--model", "-p"], ["--model=-p"], ["--model", "a b"], ["--model", ""], ["extra"]])
def test_model_argument_rejects_anything_the_cli_could_misread(argv: list[str]) -> None:
    script = _script()
    with pytest.raises(SystemExit) as stopped:
        script.parse_args(argv)
    assert stopped.value.code == 2


def test_main_uses_and_records_the_model_it_was_given(
    fake_search: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    index = tmp_path / "index.sqlite"
    index.write_bytes(b"")
    made: list[str] = []
    fakes: list[FakeClient] = []

    def cli(*, model: str) -> FakeClient:
        made.append(model)
        fakes.append(FakeClient(reply=GOOD, model=model))
        return fakes[-1]

    monkeypatch.setattr(script, "default_index_path", lambda: index)
    monkeypatch.setattr(script, "ClaudeCliClient", cli)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "OUT_PATH", tmp_path / "answers.json")
    assert script.main(["--model", "claude-opus-5"]) == 0
    assert made == ["claude-opus-5"]
    written = json.loads((tmp_path / "answers.json").read_text(encoding="utf-8"))
    assert written["model"] == "claude-opus-5"
    assert written["models_reported"] == ["claude-opus-5"]
    out = capsys.readouterr().out
    assert "model asked for: claude-opus-5" in out
    assert fakes[0].calls, "the fake search finds passages, so at least one question reaches the model"
    assert f"model calls: {len(fakes[0].calls)}" in out


def test_counting_client_counts_every_call_and_passes_the_reply_through() -> None:
    script = _script()
    inner = FakeClient(reply="text", model="claude-sonnet-5")
    counting = script.CountingClient(inner)
    assert counting.model_id == "claude-sonnet-5"
    assert counting.complete("system", "one").text == "text"
    counting.complete("system", "two")
    assert counting.calls == 2
    assert [prompt for _, prompt in inner.calls] == ["one", "two"]


# --- --only: prepare some questions again and keep the rest --------------------------------------------------------


def _existing(script: Any, model: str = "claude-sonnet-5") -> dict[str, Any]:
    entries = [
        {"question": q, "prepared_on": "2026-09-20", "label": f"old {n}", "result": {"model": model}}
        for n, q in enumerate([*script.QUESTIONS, *script.LOOKUP_QUESTIONS], start=1)
    ]
    shown = len(script.QUESTIONS)
    return {
        "model": model,
        "system_prompt_sha256": script.prompt_fingerprint(),
        "answers": entries[:shown],
        "lookup_only": entries[shown:],
    }


def test_only_keeps_every_other_entry_exactly() -> None:
    script = _script()
    existing = _existing(script)
    keep = script.kept_entries(existing, [4, 9], "claude-sonnet-5")
    every = [*script.QUESTIONS, *script.LOOKUP_QUESTIONS]
    assert list(keep) == [q for n, q in enumerate(every, start=1) if n not in {4, 9}]
    kept = [e for e in [*existing["answers"], *existing["lookup_only"]] if e["question"] in keep]
    assert all(keep[e["question"]] is e for e in kept)


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"system_prompt_sha256": "0" * 64}, "system prompt changed"),
        ({"model": "claude-opus-5"}, "was written with"),
        ({"answers": []}, "other questions"),
        ({"lookup_only": []}, "other questions"),  # a file from before the lookup-only entries
    ],
)
def test_only_refuses_a_file_it_cannot_keep_from(change: dict[str, Any], says: str) -> None:
    script = _script()
    existing = {**_existing(script), **change}
    with pytest.raises(ValueError, match=says):
        script.kept_entries(existing, [4], "claude-sonnet-5")


@pytest.mark.parametrize("number", [0, 19, -1])
def test_only_refuses_a_question_number_out_of_range(number: int) -> None:
    script = _script()
    assert len(script.QUESTIONS) + len(script.LOOKUP_QUESTIONS) == 18
    with pytest.raises(ValueError, match="question numbers from 1 to 18"):
        script.kept_entries(_existing(script), [number], "claude-sonnet-5")


def test_build_with_kept_entries_calls_the_model_only_for_the_rest(fake_search: None) -> None:
    script = _script()
    existing = _existing(script)
    keep = script.kept_entries(existing, [5], "claude-sonnet-5")
    client = FakeClient(reply=GOOD, model="claude-sonnet-5")
    data = script.build(client, script.QUESTIONS, dt.date(2026, 9, 24), keep)
    assert len(client.calls) == 1
    assert [e["label"] for e in data["answers"]][:4] == ["old 1", "old 2", "old 3", "old 4"]
    assert data["answers"][4]["prepared_on"] == "2026-09-24"
    assert data["prepared_on"] == "2026-09-24"
    assert data["label"].startswith("Prepared in advance between 20 September 2026 and 24 September 2026 ")
    assert data["models_reported"] == ["claude-sonnet-5"]


def test_main_with_only_rewrites_only_that_question(
    fake_search: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    index = tmp_path / "index.sqlite"
    index.write_bytes(b"")
    out = tmp_path / "answers.json"
    first = script.build(
        FakeClient(reply=GOOD, model="claude-sonnet-5"),
        script.QUESTIONS,
        dt.date(2026, 9, 20),
        lookups=script.LOOKUP_QUESTIONS,
    )
    out.write_text(json.dumps(first), encoding="utf-8")
    fakes: list[FakeClient] = []

    def cli(*, model: str) -> FakeClient:
        fakes.append(FakeClient(reply=GOOD, model=model))
        return fakes[-1]

    monkeypatch.setattr(script, "default_index_path", lambda: index)
    monkeypatch.setattr(script, "ClaudeCliClient", cli)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "OUT_PATH", out)
    assert script.main(["--model", "claude-sonnet-5", "--only", "3"]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert len(fakes[0].calls) == 1
    assert [e for n, e in enumerate(written["answers"], 1) if n != 3] == [
        e for n, e in enumerate(first["answers"], 1) if n != 3
    ]
    assert written["lookup_only"] == first["lookup_only"]
    assert written["answers"][2]["prepared_on"] == dt.date.today().isoformat()
    assert script.main(["--model", "claude-opus-5", "--only", "3"]) == 1, "another model: run everything again"
