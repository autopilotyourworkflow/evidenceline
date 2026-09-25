"""Prepare the website's example answers in advance, with the same pipeline as the live question box.

Usage::

    .venv/Scripts/python scripts/precompute_answers.py                            # the default model
    .venv/Scripts/python scripts/precompute_answers.py --model claude-sonnet-5    # name the model explicitly
    .venv/Scripts/python scripts/precompute_answers.py --only 4                   # rerun question 4, keep the rest
    .venv/Scripts/python scripts/precompute_answers.py --workers 5                # 5 questions at a time

Runs :func:`evidenceline.answer.answer` (the code behind ``POST /api/ask``) for each example question, with the
local Claude Code CLI as the model (headless, every tool off), and writes ``web/public/data/answers.json``. Each
entry holds the full result, including the record of every check run on the answer, the date, and a label saying
it was prepared in advance by the same pipeline with the model named. An answer that fails a check is not saved:
the result is the passages only, with the reason, exactly as the live box would show it.

Two lists of entries, each entry in the same shape: ``answers`` holds the questions the website shows as buttons
(:data:`QUESTIONS`, first and in that order, as before), and ``lookup_only`` holds every other question the question
box suggests (:data:`LOOKUP_QUESTIONS`, from ``data/suggested_questions.json``). The website shows no button for
those; it looks up a clicked or typed question in both lists, so a suggestion is answered from the prepared copy with
no live call. A file without ``lookup_only`` (from before it existed) still reads the same.

The file records the model asked for (``model``), the model names the CLI reported for the answers
(``models_reported``) and a SHA-256 of the system prompt (``system_prompt_sha256``), so a test can tell when the
prompt has changed since the answers were prepared.

Needs the local guidance index (scripts/fetch_corpus.py, scripts/build_index.py) and a Claude Code login. About
12 to 24 model calls (one per answered question, two when the first answer fails a check); questions that need no
model (a verdict, a question the guidance does not cover) make none. The script prints how many calls it made.

``--only N`` (repeatable, numbered from 1 in the order of :data:`QUESTIONS` and then :data:`LOOKUP_QUESTIONS`)
reruns only those questions and keeps every other entry exactly as it is in the existing file. It refuses unless that
file holds the same questions and was written under the same system prompt with the same model, so a kept entry is
never mixed with a changed prompt.

``--workers N`` prepares up to N questions at the same time (a thread pool; default 1, one after another). The
entries, the file and the call count are the same whatever N is: each question is answered on its own, the entries
keep the fixed order, and the file is written once, at the end.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path
from typing import Any

from evidenceline.answer import AnswerResult, ClaudeCliClient, ModelClient, ModelReply, answer
from evidenceline.answer.clients import DEFAULT_MODEL
from evidenceline.answer.prompt import SYSTEM
from evidenceline.answer.wording import DASHES
from evidenceline.guidance.search import default_index_path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "web" / "public" / "data" / "answers.json"
FIELD_SHEET = REPO_ROOT / "src" / "evidenceline" / "data" / "fds01_site" / "field_sheet.csv"

MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,99}")
"""A model id or alias as the Claude Code CLI takes it ('claude-sonnet-5', 'sonnet', 'claude-opus-5-5[1m]'). It may not
start with a hyphen, so it can never be read as another CLI option."""

QUESTIONS = (
    "What is the drinking-water limit for PFOS?",
    "Is this site contaminated?",
    "What quality checks should a lab report include?",
    "When do I have to report a suspected contaminated site to DWER?",
    "What is a conceptual site model?",
    "What are the NSW rules for PFAS in soil?",
)
"""The questions shown as buttons, in this order: the first entries of the file ("answers")."""


def lookup_questions() -> tuple[str, ...]:
    """Every question the question box suggests (``data/suggested_questions.json``) that is not one of
    :data:`QUESTIONS`, in the file's order: prepared too ("lookup_only"), so a click on a suggestion needs no live
    call."""
    raw = json.loads((resources.files("evidenceline") / "data" / "suggested_questions.json").read_text("utf-8"))
    shown = {q.casefold() for q in QUESTIONS}
    pool = [str(item["question"]) for item in raw["questions"]]
    return tuple(dict.fromkeys(q for q in pool if q.casefold() not in shown))


LOOKUP_QUESTIONS = lookup_questions()


class CountingClient:
    """Passes every call to ``inner`` and counts them, so the script can report how many model calls it made."""

    def __init__(self, inner: ModelClient) -> None:
        self.inner = inner
        self.calls = 0
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    def complete(self, system: str, prompt: str) -> ModelReply:
        with self._lock:  # questions may run in parallel (--workers)
            self.calls += 1
        return self.inner.complete(system, prompt)


def long_date(day: dt.date) -> str:
    return f"{day.day} {day:%B %Y}"


def entry_label(result: AnswerResult, model: str, day: dt.date) -> str:
    """One plain line for the website: prepared in advance, by the same pipeline, with which model, and outcome."""
    prepared = f"Prepared in advance on {long_date(day)} by the same pipeline as the live question box"
    if result.status == "answered":
        return f"{prepared}, with the model {result.model or model}. The answer passed every check before it was saved."
    if result.status == "passages_only" and result.verification.ran:
        return (
            f"{prepared}, with the model {result.model or model}. The model's answer did not pass the checks, so it "
            "was withheld and only the passages are shown."
        )
    if result.status == "guard_rail":
        return f"{prepared}. No model was called: questions like this get a fixed reply."
    if result.status == "not_covered" and result.model is None:
        return f"{prepared}. No model was called: the indexed guidance does not cover this question."
    if result.status == "not_covered":
        return f"{prepared}, with the model {result.model}. The model found the passages do not answer it."
    return f"{prepared}. No answer could be prepared: {result.explanation}"


def prompt_fingerprint() -> str:
    """SHA-256 of the system prompt the answers were written under."""
    return hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()


def _when(days: Sequence[dt.date]) -> str:
    first, last = min(days), max(days)
    return f"on {long_date(last)}" if first == last else f"between {long_date(first)} and {long_date(last)}"


def build(
    client: ModelClient,
    questions: Sequence[str],
    day: dt.date,
    keep: Mapping[str, dict[str, Any]] | None = None,
    lookups: Sequence[str] = (),
    *,
    workers: int = 1,
) -> dict[str, Any]:
    """Run every question (the buttons' ``questions``, then ``lookups``) through the pipeline, except those in
    ``keep`` (question to its existing entry), which are copied unchanged. ``workers`` questions run at the same
    time; the entries come back in the same order, with the same content, whatever it is."""
    kept = keep or {}

    def prepared(question: str) -> dict[str, Any]:
        if question in kept:
            return kept[question]
        result = answer(question, client)
        return {
            "question": question,
            "prepared_on": day.isoformat(),
            "label": entry_label(result, client.model_id, day),
            "result": result.model_dump(mode="json"),
        }

    asked = [*questions, *lookups]
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            every = list(pool.map(prepared, asked))  # map keeps the order the questions were given in
    else:
        every = [prepared(question) for question in asked]
    entries, lookup = every[: len(questions)], every[len(questions) :]
    reported = {str(e["result"]["model"]) for e in every if e["result"]["model"] is not None}
    days = [dt.date.fromisoformat(str(e["prepared_on"])) for e in every] or [day]
    return {
        "generated_by": "scripts/precompute_answers.py",
        "prepared_on": max(days).isoformat(),
        "model": client.model_id,
        "models_reported": sorted(reported),
        "system_prompt_sha256": prompt_fingerprint(),
        "prepared_with": "Claude Code CLI, headless, with every tool switched off",
        "pipeline": "evidenceline.answer.answer, the same code as the live question box (POST /api/ask)",
        "label": (
            f"Prepared in advance {_when(days)} by the same pipeline as the live question box, with the model "
            f"{client.model_id}. Every answer was checked in code before it was saved; one that failed a check "
            "would be withheld and only its passages shown."
        ),
        "answers": entries,
        "lookup_only": lookup,
    }


def kept_entries(existing: dict[str, Any], rerun: Sequence[int], model: str) -> dict[str, dict[str, Any]]:
    """The entries of ``existing`` to keep when only the questions numbered ``rerun`` (from 1) are prepared again.

    Raises ValueError when the existing file cannot be kept from: other questions, another system prompt or
    another model, or a question number out of range.
    """
    every = [*QUESTIONS, *LOOKUP_QUESTIONS]
    bad = [n for n in rerun if not 1 <= n <= len(every)]
    if bad:
        raise ValueError(f"--only takes question numbers from 1 to {len(every)}; got {bad}")
    entries = [*existing.get("answers", []), *existing.get("lookup_only", [])]
    if [e.get("question") for e in entries] != every:
        raise ValueError("the existing answers.json holds other questions; run without --only")
    if existing.get("system_prompt_sha256") != prompt_fingerprint():
        raise ValueError("the system prompt changed since answers.json was written; run without --only")
    if existing.get("model") != model:
        raise ValueError(f"answers.json was written with {existing.get('model')!r}, not {model!r}; run without --only")
    chosen = {every[n - 1] for n in rerun}
    return {str(e["question"]): e for e in entries if e["question"] not in chosen}


def _field_sheet_identifiers() -> list[str]:
    text = FIELD_SHEET.read_text(encoding="utf-8")
    return re.findall(r"^# (?:Client|Site address): (.+?)(?: \(fictional\))?$", text, flags=re.MULTILINE)


def check(data: dict[str, Any]) -> list[str]:
    """Problems that must stop the file being written: a dash in our own text, a failed answer shown, an identifier."""
    problems: list[str] = []
    for item in [*data["answers"], *data.get("lookup_only", [])]:
        result = item["result"]
        own = [item["label"], result["explanation"], result["answer"] or "", *result["notes"]]
        if any(dash in text for text in own for dash in DASHES):
            problems.append(f"{item['question']}: an em or en dash in the answer, explanation, notes or label")
        if result["status"] == "answered" and result["verification"]["passed"] is not True:
            problems.append(f"{item['question']}: marked answered without passing every check")
    rendered = json.dumps(data, ensure_ascii=False).casefold()
    problems += [
        f"contains the field-sheet identifier {i!r}" for i in _field_sheet_identifiers() if i.casefold() in rendered
    ]
    return problems


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def model_name(text: str) -> str:
    """argparse type for --model: a plain model id or alias, never something the CLI could read as an option."""
    name = text.strip()
    if not MODEL_NAME.fullmatch(name):
        raise argparse.ArgumentTypeError("use a model id or alias such as claude-sonnet-5 or sonnet")
    return name


def worker_count(text: str) -> int:
    """argparse type for --workers: a whole number from 1 to 16."""
    try:
        count = int(text)
    except ValueError:
        count = 0
    if not 1 <= count <= 16:
        raise argparse.ArgumentTypeError("use a whole number from 1 to 16")
    return count


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="precompute_answers.py",
        description="Prepare the website's example answers with the local Claude Code CLI.",
    )
    parser.add_argument(
        "--model",
        type=model_name,
        default=DEFAULT_MODEL,
        help=f"the model the Claude Code CLI uses, recorded in answers.json (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--only",
        type=int,
        action="append",
        metavar="N",
        help="rerun only question N (from 1, buttons then lookups; repeatable) and keep every other entry",
    )
    parser.add_argument(
        "--workers",
        type=worker_count,
        default=1,
        metavar="N",
        help="prepare up to N questions at the same time (default 1); the file is the same whatever N is",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if not default_index_path().exists():
        print(
            "precompute_answers: no guidance index; run scripts/fetch_corpus.py and scripts/build_index.py",
            file=sys.stderr,
        )
        return 1
    keep: dict[str, dict[str, Any]] = {}
    if args.only:
        try:
            existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            keep = kept_entries(existing, args.only, args.model)
        except (OSError, ValueError) as exc:
            print(f"precompute_answers: {exc}", file=sys.stderr)
            return 1
    client = CountingClient(ClaudeCliClient(model=args.model))
    data = build(client, QUESTIONS, dt.date.today(), keep, LOOKUP_QUESTIONS, workers=args.workers)
    problems = check(data)
    if problems:
        for line in problems:
            print(f"precompute_answers: {line}", file=sys.stderr)
        return 1
    OUT_PATH.write_bytes(render(data).encode("utf-8"))
    for item in [*data["answers"], *data["lookup_only"]]:
        result = item["result"]
        print(f"{result['status']:>13}  {item['question']}  ({result['verification']['summary']})")
    reported = ", ".join(data["models_reported"]) or "none (no model was called)"
    print(f"model asked for: {data['model']}; models reported: {reported}; model calls: {client.calls}")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
