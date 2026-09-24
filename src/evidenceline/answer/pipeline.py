"""answer(question): the "Ask the guidelines" pipeline behind the website's question box.

1. Redact the question: company and site names found by :mod:`evidenceline.answer.names` ('Harbourline Logistics
   Pty Ltd', 'my client Redgum', 'Kwinana Terminal') and the built-in patterns (emails, WA street addresses, lot
   numbers, phone numbers).
2. If it asks for a PFAS drinking-water value, take BOTH rules' values from ``guidelines.json`` (never from text).
3. Search the indexed guidance for up to 8 passages. "Not covered" stops here, and so does a question asking for
   a verdict (is the site contaminated, is the water safe): both return without calling a model. The one exception
   is a borderline "not covered" (the search's closest passages are a near miss, see
   :data:`evidenceline.guidance.search.BORDERLINE_COVERAGE`): with live answers on, those passages go to the model
   with an added instruction to reply NOT_COVERED unless they answer the question. Another state's rules, prices,
   an instruction to the system, a document that is not indexed, or anything below the band never reach a model.
4. Otherwise ask the model for 2 to 4 cited sentences from the passages and values only.
5. Check the answer in code: every check in :mod:`evidenceline.answer.verify`, plus two of the prompt's plain-wording
   rules (every sentence under :data:`~evidenceline.answer.prompt.MAX_SENTENCE_WORDS` words, and no run of more
   than :data:`~evidenceline.answer.prompt.MAX_QUOTED_WORDS` words copied from a passage). If any check fails, the
   model is asked once more; if that fails too, the answer is withheld and only the passages are shown, with the
   names of the checks it failed. Its text is never shown, not even in part.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from evidenceline.answer import routing
from evidenceline.answer.clients import ModelClient, ModelPausedError, ModelUnavailableError
from evidenceline.answer.context import PassageText, passage_texts
from evidenceline.answer.models import (
    AnswerResult,
    AnswerStatus,
    Citation,
    GuidelineValue,
    Verification,
    VerificationCheck,
)
from evidenceline.answer.names import find_names
from evidenceline.answer.prompt import MAX_QUOTED_WORDS, MAX_SENTENCE_WORDS, NOT_COVERED, SYSTEM, build_prompt
from evidenceline.answer.values import describe, guideline_values
from evidenceline.answer.verify import citations, sentences, verify
from evidenceline.errors import EvidencelineError
from evidenceline.guidance.models import GuidanceSearch, Passage
from evidenceline.guidance.scope import NUMERIC_NOTE
from evidenceline.guidance.search import default_index_path, search_guidelines
from evidenceline.redact import RedactionConfig, Redactor
from evidenceline.screening import INVESTIGATION_LEVEL, RULE_CHOICE

MAX_PASSAGES = 8

GUARD_RAIL_REPLY = (
    "Evidenceline does not decide whether a site is contaminated or whether water is safe. A guideline value is "
    "an investigation level, not a finding that water is unsafe or a site is contaminated: a result above it "
    "means look further. In Western Australia, DWER classifies sites under the Contaminated Sites Act 2003 on the "
    "evidence. The passages below are what the indexed guidance says on the subject."
)
LIVE_OFF_NOTE = "Live answers are switched off on this server, so only the passages are shown."
VALUE_NOTE = (
    "This question asks for a value. Evidenceline never reads guideline values from passage text, because tables "
    "extract badly. PFAS drinking-water values come from its verified table; for any other value, read it on the "
    "cited page of the official document."
)
VALUES_SOURCE_NOTE = (
    "The guideline values come from Evidenceline's verified table, with document, table and page, never from the "
    "passages. Both rules are shown side by side."
)
OTHER_MEDIUM_NOTE = (
    "Only PFAS drinking-water values are loaded, so no soil, sediment, recreational or ecological values are given."
)
BORDERLINE_INSTRUCTION = (
    "The search found no passage that clearly covers this question: the passages above are the closest ones. "
    f"Answer only if they answer the question itself. If they do not, reply with exactly {NOT_COVERED} and nothing "
    "else."
)
"""Added to the prompt when the passages are a borderline match (see step 3)."""
BORDERLINE_ANSWERED = (
    " The search rated these passages a near miss, so the model was told to reply that they do not cover the "
    "question unless they answer it."
)
BORDERLINE_WITHHELD = (
    " The search found no passage that clearly covers this question: these passages are its near miss, the closest "
    "it found, so read them as possibly related, not as a match."
)
NOTHING_TO_CHECK = Verification(
    ran=False, passed=None, checks=[], summary="No model answer was written, so there was nothing to check."
)


def _redact(question: str) -> tuple[str, int]:
    """The question with identifiers replaced, and how many were replaced. A fresh redactor per question."""
    counts: Counter[str] = Counter()
    config = RedactionConfig(find_names(question), "Names found in the question, and the built-in patterns.")
    redactor = Redactor(config, None)
    return redactor.redact_text(question, counts), sum(counts.values())


def _citations(passages: Sequence[Passage], cited: set[int]) -> list[Citation]:
    return [
        Citation(
            number=number,
            cited=number in cited,
            document_id=p.document_id,
            document=p.document_title,
            edition=p.edition,
            wa_status=p.wa_status,
            printed_page=p.printed_page,
            pdf_page=p.pdf_page,
            location=p.location,
            section=p.section,
            excerpt=p.excerpt,
            licence=p.licence,
            notice=p.notice,
            link=p.link,
        )
        for number, p in enumerate(passages, start=1)
    ]


class _Question:
    """One question on its way through the pipeline: holds what every result needs."""

    def __init__(self, question: str, index_path: Path | None) -> None:
        self.text, self.redactions = _redact(question)
        self.index_path = index_path or default_index_path()
        analytes = routing.drinking_water_analytes(self.text)
        self.values: list[GuidelineValue] = guideline_values(analytes) if analytes else []
        self.notes: list[str] = []
        if self.redactions:
            self.notes.append(
                f"{self.redactions} identifier(s) in the question were replaced with placeholders before it was "
                "searched or sent to a model."
            )
        if self.values:
            self.notes += [VALUES_SOURCE_NOTE, INVESTIGATION_LEVEL, RULE_CHOICE]
        elif routing.names_other_medium(self.text):
            self.notes.append(OTHER_MEDIUM_NOTE)
        self.passages: list[Passage] = []

    def result(
        self,
        status: AnswerStatus,
        explanation: str,
        *,
        answer: str | None = None,
        cited: set[int] | None = None,
        verification: Verification = NOTHING_TO_CHECK,
        model: str | None = None,
    ) -> AnswerResult:
        return AnswerResult(
            question=self.text,
            question_redactions=self.redactions,
            status=status,
            explanation=explanation,
            answer=answer,
            citations=_citations(self.passages, cited or set()),
            guideline_values=self.values,
            notes=self.notes,
            verification=verification,
            model=model,
        )

    def search(self) -> GuidanceSearch:
        found = search_guidelines(self.text, MAX_PASSAGES, index_path=self.index_path)
        self.passages = list(found.passages)
        for note in found.notes:
            shown = VALUE_NOTE if note == NUMERIC_NOTE else note
            if shown not in self.notes and not (self.values and note == NUMERIC_NOTE):
                self.notes.append(shown)
        return found


def answer(
    question: str,
    client: ModelClient | None,
    *,
    index_path: Path | None = None,
    off_note: str = LIVE_OFF_NOTE,
) -> AnswerResult:
    """Answer one question from the indexed guidance, or say plainly why not. Never raises for bad input.

    ``client`` None means live answers are off: the passages are returned with ``off_note``.
    """
    item = _Question(question, index_path)
    try:
        found = item.search()
    except EvidencelineError as exc:
        return item.result("error", str(exc))
    except sqlite3.Error:
        return item.result("error", "The guidance index could not be read. It needs to be rebuilt on the server.")

    if found.status == "not covered":
        closest = list(found.closest_passages)
        if closest and client is not None and not routing.asks_for_verdict(item.text):
            item.passages = closest
            return _ask_model(item, client, borderline=True)
        extra = " The verified guideline values are shown below." if item.values else ""
        return item.result("not_covered", found.explanation + extra)
    if routing.asks_for_verdict(item.text):
        return item.result(
            "guard_rail",
            "This question asks for a verdict that Evidenceline does not give. No model was called; the reply "
            "below is fixed, and the passages are from the indexed guidance.",
            answer=GUARD_RAIL_REPLY,
        )
    if client is None:
        item.notes.append(off_note)
        return item.result("passages_only", "Passages from the indexed guidance, best match first. " + off_note)
    return _ask_model(item, client)


RETRY_NOTE = (
    "Your previous answer was not shown, because code found these problems: {reasons}. Write the answer again, "
    "following every rule."
)
"""Added to the prompt for the one second attempt. It names the failed checks, not the previous answer."""


def _reasons(record: Verification) -> str:
    return "; ".join(f"{c.name}: {c.detail.rstrip('.')}" for c in record.checks if not c.passed)


_CITATION = re.compile(r"\[(?=G?\d)[^\]]*\]")
_WORD = re.compile(r"[^\s]*[A-Za-z0-9][^\s]*")
"""A word as the sentence-length rule counts it: anything between spaces that holds a letter or digit, so a full
stop left alone after a citation is not a word."""
_RIGHT_QUOTE = chr(0x2019)


def _tokens(text: str) -> list[str]:
    """Lower-case words and numbers, citations left out; a typographic apostrophe is read as a plain one."""
    return re.findall(r"[a-z0-9]+", _CITATION.sub(" ", text).replace(_RIGHT_QUOTE, "'").lower())


def _check_sentence_length(answer: str) -> VerificationCheck:
    """Rule 2 of the prompt: every sentence under MAX_SENTENCE_WORDS words, citations not counted."""
    found = sentences(answer)
    counts = [len(_WORD.findall(_CITATION.sub(" ", s))) for s in found]
    failures = [
        f"sentence {n} of {len(found)} has {words} words"
        for n, words in enumerate(counts, 1)
        if words >= MAX_SENTENCE_WORDS
    ]
    if failures:
        detail = (
            "; ".join(failures)
            + f", and every sentence must be under {MAX_SENTENCE_WORDS} words: split or shorten each long one."
        )
        return VerificationCheck(name="short sentences", passed=False, detail=detail)
    return VerificationCheck(
        name="short sentences",
        passed=True,
        detail=f"All {len(found)} sentence(s) are under {MAX_SENTENCE_WORDS} words.",
    )


def _check_copied_runs(answer: str, passages: Sequence[PassageText]) -> VerificationCheck:
    """Rule 9 of the prompt: no run of more than MAX_QUOTED_WORDS words copied from a passage. The detail names the
    sentence and the passage, never the words."""
    size = MAX_QUOTED_WORDS + 1
    runs = {
        p.number: {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}
        for p in passages
        for words in [_tokens(p.body)]
    }
    failures: list[str] = []
    for n, sentence in enumerate(sentences(answer), 1):
        words = _tokens(sentence)
        pieces = {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}
        failures += [
            f"sentence {n} copies more than {MAX_QUOTED_WORDS} words in a row from passage {number}: paraphrase it"
            for number, held in runs.items()
            if pieces & held
        ]
    if failures:
        return VerificationCheck(name="no long quotes", passed=False, detail="; ".join(failures) + ".")
    return VerificationCheck(
        name="no long quotes",
        passed=True,
        detail=f"No run of more than {MAX_QUOTED_WORDS} words is copied from a passage.",
    )


def _check_answer(
    answer: str, passages: Sequence[PassageText], values: Sequence[GuidelineValue], value_lines: Sequence[str]
) -> Verification:
    """Every check in :func:`evidenceline.answer.verify.verify`, then the two plain-wording checks, in one record."""
    record = verify(answer, passages, values, value_lines)
    if not answer.strip():
        return record
    checks = [*record.checks, _check_sentence_length(answer), _check_copied_runs(answer, passages)]
    failed = [c for c in checks if not c.passed]
    if failed:
        summary = f"{len(failed)} of {len(checks)} checks failed: " + "; ".join(c.name for c in failed) + "."
    else:
        summary = f"All {len(checks)} checks passed."
    return Verification(ran=True, passed=not failed, checks=checks, summary=summary)


def _ask_model(item: _Question, client: ModelClient, *, borderline: bool = False) -> AnswerResult:
    """Ask the model; if its answer fails a check, ask once more with the names of the failed checks. Each answer
    is checked in full; one that fails is never shown. ``borderline``: the passages are the search's near miss, and
    the prompt says so (BORDERLINE_INSTRUCTION)."""
    texts = passage_texts(item.passages, item.index_path)
    value_lines = [describe(value) for value in item.values]
    prompt = build_prompt(item.text, texts, value_lines)
    if borderline:
        prompt = "\n\n".join([prompt, BORDERLINE_INSTRUCTION])
    try:
        reply = client.complete(SYSTEM, prompt)
    except ModelPausedError as exc:
        return item.result("paused", f"{exc} The passages are shown instead.", model=client.model_id)
    except ModelUnavailableError as exc:
        return item.result("error", f"{exc} The passages are shown instead.", model=client.model_id)
    if NOT_COVERED in reply.text:
        return _not_covered(item, reply.model, borderline)
    record = _check_answer(reply.text, texts, item.values, value_lines)
    attempts = 1
    if not record.passed:
        retry = "\n\n".join([prompt, RETRY_NOTE.format(reasons=_reasons(record))])
        try:
            second = client.complete(SYSTEM, retry)
        except ModelUnavailableError:  # includes a pause: the first answer's result stands
            return _withheld(item, record, reply.model, attempts, borderline)
        attempts = 2
        if NOT_COVERED in second.text:
            return _not_covered(item, second.model, borderline)
        reply, record = second, _check_answer(second.text, texts, item.values, value_lines)
        if not record.passed:
            return _withheld(item, record, reply.model, attempts, borderline)
    cited, _, _ = citations(reply.text)
    retried = " The first answer failed a check and was not shown; this is the second." if attempts == 2 else ""
    near = BORDERLINE_ANSWERED if borderline else ""
    return item.result(
        "answered",
        f"Written by {reply.model} from the numbered passages only, then checked in code: every citation exists, "
        "every sentence is cited and every number was found in a cited passage or in the verified guideline "
        f"values.{retried}{near} Open a citation to read the source.",
        answer=reply.text,
        cited=set(cited),
        verification=record,
        model=reply.model,
    )


def _not_covered(item: _Question, model: str, borderline: bool = False) -> AnswerResult:
    found = (
        "The search found only near-miss passages, and the model ({model}) found that they do not answer this "
        "question. They are shown so you can check."
        if borderline
        else "The model ({model}) found that the passages do not answer this question. The closest passages are "
        "shown so you can check."
    )
    return item.result("not_covered", found.format(model=model), model=model)


def _withheld(
    item: _Question, record: Verification, model: str, attempts: int, borderline: bool = False
) -> AnswerResult:
    failed = [c for c in record.checks if not c.passed]
    tries = "Two answers were written but both were withheld" if attempts == 2 else "An answer was written but withheld"
    last = "the second one " if attempts == 2 else "it "
    return item.result(
        "passages_only",
        f"{tries}, because {last}did not pass {len(failed)} of the {len(record.checks)} checks in code "
        f"({_reasons(record)}). No answer text is shown. The passages it was given are shown instead."
        + (BORDERLINE_WITHHELD if borderline else ""),
        verification=record,
        model=model,
    )
