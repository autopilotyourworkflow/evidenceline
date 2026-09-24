"""Check a model's answer in code before anyone sees it. No model is involved in any check.

Checks, each reported with what it looked at:

1. **Citations exist.** Every marker such as [2] names a passage that was given, every [G1] a guideline value
   that was given, and there is at least one.
2. **Every sentence is cited.** A new sentence starts after a full stop, question mark or exclamation mark
   (whatever letter follows, except after an abbreviation such as 'p.' or 'e.g.') and at every line break.
3. **Every number is traced.** Each number in the answer must appear in the text of a passage the answer cites, or
   in the guideline values given. A concentration (in any unit spelling: ug/L, micrograms per litre, ng/litre,
   parts per trillion...) must be the value of a guideline marker cited in its own sentence, and when the words
   before it name a rule (NEMP 3.0, or the current values) that marker must belong to that rule. Evidenceline never
   takes a concentration from extracted text, because tables extract badly. A percentage must match a percentage.
4. **Both rules shown.** When guideline values were given, each one is stated in a sentence that cites its marker.
5. **No verdict wording**, such as "is safe", "fine to drink" or "is therefore contaminated".
6. **No rule picked**, when guideline values were given ("should use", "takes precedence", "the one to rely on").
7. **No dashes** (em, en, horizontal bar and their look-alikes).

The details of a failed check never quote the answer's words, so an answer that is withheld is not shown in pieces:
they name the number that failed, the sentence number, or the dash's code point.

Known limits: numbers written as words ("two") are not read; a bare number is traced to any cited passage, not to
the passage cited in its own sentence.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from evidenceline.answer.context import PassageText
from evidenceline.answer.models import GuidelineValue, Verification, VerificationCheck
from evidenceline.answer.numbers import Quantity, read_numbers
from evidenceline.answer.wording import dashes, rule_picks, verdicts
from evidenceline.units import parse_decimal

__all__ = ["Quantity", "citations", "read_numbers", "sentences", "verify"]

_BRACKET = re.compile(r"\[(?=G?\d)([^\]]*)\]")
"""Anything in square brackets that starts like a citation: [1], [G2], [1, 3], [1-3]."""
_MARKER = re.compile(r"(G?)(\d+)")
_WELL_FORMED = re.compile(r"G?\d+(?:\s*,\s*G?\d+)*")
_ABBREVIATIONS = frozenset(
    {
        "p", "pp", "e.g", "i.e", "eg", "ie", "etc", "no", "nos", "s", "ss", "cl", "ch", "sch", "vol", "approx",
        "fig", "figs", "tab", "sec", "secs", "ref", "refs", "cf", "al", "vs", "pg", "para", "paras", "app", "appx",
        "ed", "eds", "reg", "regs", "pt", "div", "art",
    }
)  # fmt: skip
_LEADING_MARKERS = re.compile(r"((?:\[G?\d+(?:\s*,\s*G?\d+)*\]\s*)+[.!?]?)\s*(.*)", re.DOTALL)
"""Citations at the start of a sentence belong to the sentence before: 'It is needed. [1] Next ...'."""
_SENTENCE_END = re.compile(r"[.!?]+\s+(?=\S)")
_LINE_BREAK = re.compile(r"\s*\n\s*")
_NEMP_30 = re.compile(r"\bNEMP\s*(?:v(?:ersion)?\s*)?3\.0\b|\bversion\s+3\.0\b", re.IGNORECASE)
_CURRENT = re.compile(
    r"\bcurrent\s+(?:national\s+|NHMRC\s+|ADWG\s+|drinking[\s-]water\s+|australian\s+)*"
    r"(?:rule|rules|value|values|guideline|guidelines|ADWG|limit|limits|criteri(?:on|a))\b|"
    r"\bNEMP\s*(?:v(?:ersion)?\s*)?3\.1\b|\bversion\s+3\.1\b|\b(?:updated|update)\s+(?:in\s+)?2025\b|\b2025\s+update\b",
    re.IGNORECASE,
)


def citations(text: str) -> tuple[list[int], list[str], list[str]]:
    """(passage numbers, guideline markers such as 'G1', malformed bracket contents) in the order they appear."""
    passages: list[int] = []
    values: list[str] = []
    malformed: list[str] = []
    for match in _BRACKET.finditer(text):
        inside = match.group(1).strip()
        if not _WELL_FORMED.fullmatch(inside):
            malformed.append(f"[{inside}]")
            continue
        for prefix, digits in _MARKER.findall(inside):
            if prefix:
                values.append(f"G{digits}")
            else:
                passages.append(int(digits))
    return passages, values, malformed


def _split_block(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        before = text[start : match.start()].split()
        last = before[-1].rstrip(".").lower() if before else ""
        if last in _ABBREVIATIONS or (len(last) == 1 and last.isalpha()):
            continue
        parts.append(text[start : match.end()].strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def sentences(text: str) -> list[str]:
    """Split into sentences at every full stop, question mark or exclamation mark followed by a space (not after
    abbreviations such as 'p.' or 'e.g.'), and at every line break."""
    parts = [part for block in _LINE_BREAK.split(text) for part in _split_block(block)]
    merged: list[str] = []
    for part in parts:
        leading = _LEADING_MARKERS.fullmatch(part)
        if merged and leading:
            merged[-1] = f"{merged[-1]} {leading.group(1).strip()}"
            part = leading.group(2).strip()  # noqa: PLW2901 - the rest of the sentence, without the moved markers
        if part:
            merged.append(part)
    return merged


def _check(name: str, failures: Sequence[str], passed_detail: str) -> VerificationCheck:
    if failures:
        return VerificationCheck(name=name, passed=False, detail="; ".join(failures) + ".")
    return VerificationCheck(name=name, passed=True, detail=passed_detail)


def _check_citations(text: str, passage_count: int, values: Sequence[GuidelineValue]) -> VerificationCheck:
    numbers, markers, malformed = citations(text)
    known_markers = {v.marker for v in values}
    failures = [f"citation {m} is not in the form [1] or [G1]" for m in malformed]
    failures += [f"[{n}] cites a passage that was not given" for n in numbers if not 1 <= n <= passage_count]
    failures += [f"[{m}] cites a guideline value that was not given" for m in markers if m not in known_markers]
    if not numbers and not markers:
        failures.append("the answer cites nothing")
    detail = f"{len(numbers) + len(markers)} citation(s), each naming one of the {passage_count} passage(s)"
    if values:
        detail += f" or {len(values)} guideline value(s)"
    return _check("citations exist", failures, detail + ".")


def _check_sentences(text: str) -> VerificationCheck:
    found = sentences(text)
    uncited = [n for n, s in enumerate(found, start=1) if not _BRACKET.search(s)]
    failures = [f"sentence {n} of {len(found)} has no citation" for n in uncited]
    return _check("every sentence cited", failures, f"All {len(found)} sentence(s) carry a citation.")


@dataclass(frozen=True, slots=True)
class _Sentence:
    text: str
    markers: frozenset[str]
    numbers: list[Quantity]


def _read_sentences(text: str) -> list[_Sentence]:
    read: list[_Sentence] = []
    for sentence in sentences(text):
        _, markers, _ = citations(sentence)
        read.append(_Sentence(sentence, frozenset(markers), read_numbers(sentence)))
    return read


def _given(values: Sequence[GuidelineValue]) -> list[GuidelineValue]:
    return [v for v in values if v.available and v.value is not None]


def _named_rule(before: str) -> str | None:
    """The rule the words before a concentration name, the one named last: 'nemp-3.0', 'current', or None."""
    named = [(m.end(), "nemp-3.0") for m in _NEMP_30.finditer(before)]
    named += [(m.end(), "current") for m in _CURRENT.finditer(before)]
    return max(named)[1] if named else None


def _concentration_failure(
    number: Quantity, sentence: _Sentence, previous_end: int, values: Sequence[GuidelineValue]
) -> str | None:
    if not any(number.canonical == parse_decimal(v.value or "0") for v in _given(values)):
        return (
            f"{number.text} is not one of the verified guideline values (concentrations are never taken from "
            "passage text)"
        )
    own = [
        v for v in _given(values) if v.marker in sentence.markers and parse_decimal(v.value or "0") == number.canonical
    ]
    if not own:
        return f"{number.text} is not the value of a guideline marker cited in its own sentence"
    rule = _named_rule(sentence.text[previous_end : number.start])
    if rule is not None and all(v.rule != rule for v in own):
        cited = ", ".join(sorted(v.marker for v in own))
        return f"{number.text} is attributed to a different rule from the one its marker {cited} belongs to"
    return None


def _check_numbers(
    text: str, passages: Sequence[PassageText], values: Sequence[GuidelineValue], value_lines: Sequence[str]
) -> VerificationCheck:
    cited, _, _ = citations(text)
    passage_numbers = [q for p in passages if p.number in cited for q in read_numbers(p.text)]
    value_numbers = [q for line in value_lines for q in read_numbers(line)]
    traceable = passage_numbers + value_numbers
    failures: list[str] = []
    shown: list[str] = []
    for sentence in _read_sentences(text):
        previous_end = 0
        for number in sentence.numbers:
            shown.append(number.text)
            if number.kind == "water":
                failure = _concentration_failure(number, sentence, previous_end, values)
                previous_end = number.start + len(number.text)
            elif number.kind == "soil":
                failure = f"{number.text} is a soil concentration; no soil values were given"
            elif number.kind == "unknown":
                failure = f"{number.text} has a unit Evidenceline does not read as a concentration"
            elif not _matches(number, traceable):
                failure = f"{number.text} does not appear in a cited passage or in the guideline values"
            else:
                failure = None
            if failure is not None:
                failures.append(failure)
    if not shown:
        return VerificationCheck(name="numbers traced", passed=True, detail="The answer contains no numbers.")
    return _check(
        "numbers traced", failures, f"Every number ({', '.join(shown)}) was found in a cited passage or value."
    )


def _matches(number: Quantity, sources: Sequence[Quantity]) -> bool:
    if number.kind == "label":
        return any(source.kind == "label" and source.text == number.text for source in sources)
    if number.kind == "plain":
        # A bare number never matches a concentration: "0.07" is not traced by "0.07 ug/L" in a passage.
        return any(source.kind in ("plain", "percent") and source.raw == number.raw for source in sources)
    return any(source.kind == number.kind and source.canonical == number.canonical for source in sources)


def _check_both_rules(text: str, values: Sequence[GuidelineValue]) -> VerificationCheck | None:
    given = _given(values)
    if not given:
        return None
    read = _read_sentences(text)

    def stated(value: GuidelineValue) -> bool:
        amount = parse_decimal(value.value or "0")
        return any(
            value.marker in s.markers and any(q.kind == "water" and q.canonical == amount for q in s.numbers)
            for s in read
        )

    failures = [
        f"{v.rule_name}: {v.value} {v.unit} ({v.marker}) is not stated in a sentence citing {v.marker}"
        for v in given
        if not stated(v)
    ]
    return _check("both rules shown", failures, f"All {len(given)} guideline value(s) given are stated.")


def _check_phrases(
    name: str, finder: Callable[[str], list[str]], answer: str, what: str, passed_detail: str
) -> VerificationCheck:
    """A wording check. A failure names the sentence numbers, never the words, so a withheld answer is not shown."""
    where = [str(n) for n, sentence in enumerate(sentences(answer), start=1) if finder(sentence)]
    if where:
        return VerificationCheck(name=name, passed=False, detail=f"Found {what} in sentence {', '.join(where)}.")
    if finder(answer):
        return VerificationCheck(name=name, passed=False, detail=f"Found {what}.")
    return VerificationCheck(name=name, passed=True, detail=passed_detail)


def verify(
    answer: str,
    passages: Sequence[PassageText],
    values: Sequence[GuidelineValue],
    value_lines: Sequence[str],
) -> Verification:
    """Run every check on ``answer``. ``value_lines`` are the guideline value lines exactly as the model saw them."""
    if not answer.strip():
        check = VerificationCheck(name="answer present", passed=False, detail="The model returned no text.")
        return Verification(ran=True, passed=False, checks=[check], summary="The model returned no text.")
    checks = [
        _check_citations(answer, len(passages), values),
        _check_sentences(answer),
        _check_numbers(answer, passages, values, value_lines),
    ]
    both = _check_both_rules(answer, values)
    if both is not None:
        checks.append(both)
        checks.append(
            _check_phrases(
                "no rule picked",
                rule_picks,
                answer,
                "wording that picks a rule",
                "No wording that picks one rule over the other.",
            )
        )
    checks.append(
        _check_phrases(
            "no verdict wording",
            verdicts,
            answer,
            "verdict wording",
            "No wording that says water is safe or unsafe, or that a site is or is not contaminated.",
        )
    )
    checks.append(_check("no dashes", [f"contains {d}" for d in dashes(answer)], "No dashes."))
    failed = [c for c in checks if not c.passed]
    if failed:
        summary = f"{len(failed)} of {len(checks)} checks failed: " + "; ".join(c.name for c in failed) + "."
    else:
        summary = f"All {len(checks)} checks passed."
    return Verification(ran=True, passed=not failed, checks=checks, summary=summary)
