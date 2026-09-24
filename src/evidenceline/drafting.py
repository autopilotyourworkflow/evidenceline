"""Code fills in the numbers: Claude writes placeholders, and this module replaces each one with the exact value.

The model never types a number. It writes a placeholder such as ``{PFOS|MB2|Sep 2025}`` and
:func:`fill_placeholders` swaps it for ``0.038 ug/L``, taken from the lab row or guideline table, and returns a
numbered list of the sources it used. Everything here is deterministic; no language model is called.

Placeholder grammar (fields are separated by ``|``, spaces around fields are ignored, case does not matter):

======================================  ==============================================================
``{ANALYTE|WELL|DATE}``                 the measured result, for example ``0.038 ug/L``; a non-detect
                                        is written as the lab reported it, for example ``<0.001 ug/L``
``{sum PFOS+PFHxS|WELL|DATE}``          the sum of PFOS and PFHxS, computed by code
``{lor|ANALYTE|WELL|DATE}``             the detection limit for that result
``{limit|ANALYTE|RULE}``                a guideline value; RULE is ``nemp-3.0`` or ``current`` (no default)
``{change|ANALYTE|WELL|FROM|TO}``       the change from the FROM round to the later TO round, with
                                        direction and percent, for example ``a fall of 0.003 ug/L (7.3%)``
======================================  ==============================================================

DATE, FROM and TO accept ``2025-09-16``, ``16 September 2025`` or ``Sep 2025``. ANALYTE in ``change`` may also be
``sum PFOS+PFHxS``. Anything else in braces is an error that lists the valid forms.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from evidenceline.dataset import SUM_KEY, SUM_MEMBERS, Dataset, LabResult, Round, default_dataset, long_date
from evidenceline.errors import EvidencelineError
from evidenceline.models import SourceRef
from evidenceline.screening import (
    EQUAL_IS_NOT_ABOVE,
    FOOTNOTE_A,
    INVESTIGATION_LEVEL,
    RULE_CHOICE,
    lab_source,
    limit_source,
)
from evidenceline.textparse import SpanSet, find_dates
from evidenceline.units import CANONICAL_UNIT, fmt, fmt_ug, percent_change

VALID_FORMS = (
    "{ANALYTE|WELL|DATE} for a measured result, for example {PFOS|MB2|Sep 2025}",
    "{sum PFOS+PFHxS|WELL|DATE} for the sum of PFOS and PFHxS, for example {sum PFOS+PFHxS|MB2|2025-09-16}",
    "{lor|ANALYTE|WELL|DATE} for a detection limit, for example {lor|PFOA|MB2|Sep 2025}",
    "{limit|ANALYTE|RULE} for a guideline value, RULE being nemp-3.0 or current, for example {limit|PFOS|current} "
    "or {limit|PFOS+PFHxS|nemp-3.0}",
    "{change|ANALYTE|WELL|FROM|TO} for the change from an earlier round to a later one, for example "
    "{change|PFOS|MB2|Nov 2024|Sep 2025}",
)
"""The placeholder forms, as listed in error messages and in the tool description."""

_DATE_FORMS = "Dates may be written as 2025-09-16, 16 September 2025 or Sep 2025."

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_SUM_FIELD = re.compile(
    r"(?:sum\s*(?:of\s*)?)?(?:pfos\s*(?:\+|and)\s*pfhxs|pfhxs\s*(?:\+|and)\s*pfos)",
    re.IGNORECASE,
)

FillKind = Literal["result", "detection limit", "sum", "guideline limit", "change"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NumberedSource(SourceRef):
    """A source with the number the filled values refer to."""

    number: int = Field(description="Source number, 1-based, as used in annotated_text.")


class FilledValue(_Model):
    number: int = Field(description="1-based position of the placeholder in the text.")
    placeholder: str = Field(description="The placeholder exactly as written.")
    kind: FillKind
    text: str = Field(description="What replaced the placeholder, for example '0.038 ug/L'.")
    value: str | None = Field(
        description="Exact value in ug/L. For a change it is signed (negative for a fall). Null for a non-detect."
    )
    start: int = Field(description="Start offset of the replacement in the filled text.")
    end: int = Field(description="End offset (exclusive) of the replacement in the filled text.")
    rule: str | None = Field(default=None, description="The rule a guideline value belongs to.")
    source_numbers: list[int] = Field(description="Numbers of the sources this value came from.")
    explanation: str


class FilledText(_Model):
    well: str
    text: str = Field(description="The text with every placeholder replaced. Pass this to check_paragraph.")
    annotated_text: str = Field(description="The same text with source numbers in brackets after each value.")
    values: list[FilledValue]
    sources: list[NumberedSource]
    next_step: str
    notes: list[str]


@dataclass(frozen=True, slots=True)
class _Fill:
    kind: FillKind
    text: str
    value: str | None
    sources: tuple[SourceRef, ...]
    explanation: str
    rule: str | None = None


# --- field parsing -------------------------------------------------------------------------------------------------


def _quantity(ds: Dataset, field: str) -> str:
    """An analyte name as stored, or SUM_KEY for the sum of PFOS and PFHxS."""
    if _SUM_FIELD.fullmatch(field.strip()):
        return SUM_KEY
    for known in ds.analytes():
        if known.lower() == field.strip().lower():
            return known
    raise EvidencelineError(
        f"unknown analyte {field.strip()!r}. Known analytes: {', '.join(ds.analytes())}, "
        "or 'sum PFOS+PFHxS' for the sum of PFOS and PFHxS."
    )


def _placeholder_well(ds: Dataset, field: str, well: str) -> str:
    named = ds.resolve_well(field)
    if named != well:
        raise EvidencelineError(
            f"this call is for well {well}, but the placeholder names {named}. Fill one well per call, so that "
            "check_paragraph can check the text against that well."
        )
    return named


def _find_round(ds: Dataset, well: str, field: str) -> Round:
    rounds = ds.rounds(well)
    text = field.strip()
    matches: list[Round]
    try:
        exact = dt.date.fromisoformat(text)
    except ValueError:
        found, unclear = find_dates(text, SpanSet())
        if len(found) != 1 or unclear or found[0].text.strip(" .,") != text.strip(" .,"):
            raise EvidencelineError(f"could not read the date {text!r}. {_DATE_FORMS}") from None
        matches = found[0].resolve(rounds)
    else:
        matches = [r for r in rounds if r.date == exact]
    available = ", ".join(f"{long_date(r.date)} ({r.date.isoformat()})" for r in rounds)
    if not matches:
        raise EvidencelineError(f"no {well} round on {text!r}. Rounds with data: {available}.")
    if len(matches) > 1:
        raise EvidencelineError(f"{text!r} matches more than one round; give the day. Rounds with data: {available}.")
    return matches[0]


def _member(round_: Round, analyte: str) -> LabResult:
    result = round_.results.get(analyte)
    if result is None:
        raise EvidencelineError(f"{analyte} was not analysed on {long_date(round_.date)}.")
    return result


def _detected_value(round_: Round, quantity: str) -> tuple[Decimal, tuple[LabResult, ...]]:
    """The value of an analyte or the PFOS + PFHxS sum in one round. Raises if any part was not detected."""
    names = SUM_MEMBERS if quantity == SUM_KEY else (quantity,)
    rows = tuple(_member(round_, name) for name in names)
    missing = [r for r in rows if r.value is None]
    if missing:
        detail = ", ".join(f"{r.analyte} (<{fmt(r.detection_limit)} {CANONICAL_UNIT})" for r in missing)
        raise EvidencelineError(
            f"not detected on {long_date(round_.date)}: {detail}. A non-detect has no single value, so this "
            "cannot be filled. Use {ANALYTE|WELL|DATE} for each analyte instead, which writes the result as "
            "the lab reported it."
        )
    return sum((r.value for r in rows if r.value is not None), Decimal(0)), rows


# --- resolvers -----------------------------------------------------------------------------------------------------


def _sum_source(round_: Round, rows: tuple[LabResult, ...], total: Decimal) -> SourceRef:
    terms = " + ".join(f"{r.analyte} {fmt(r.value)}" for r in rows if r.value is not None)
    return SourceRef(
        kind="computed sum",
        reference=f"computed by code: {terms} = {fmt_ug(total)} on {long_date(round_.date)} "
        f"({round_.lab_report}, {round_.file} rows {' + '.join(str(r.row) for r in rows)})",
        value=fmt(total),
        analyte=SUM_KEY,
        date=round_.date.isoformat(),
        lab_report=round_.lab_report,
        file=round_.file,
        rows=[r.row for r in rows],
    )


def _fill_result(ds: Dataset, well: str, fields: list[str]) -> _Fill:
    quantity = _quantity(ds, fields[0])
    _placeholder_well(ds, fields[1], well)
    round_ = _find_round(ds, well, fields[2])
    if quantity == SUM_KEY:
        total, rows = _detected_value(round_, SUM_KEY)
        return _Fill(
            kind="sum",
            text=fmt_ug(total),
            value=fmt(total),
            sources=(_sum_source(round_, rows, total), *(lab_source(r) for r in rows)),
            explanation=f"Sum of PFOS and PFHxS on {long_date(round_.date)}: "
            f"{' + '.join(fmt(r.value) for r in rows if r.value is not None)} = {fmt_ug(total)}, computed by code.",
        )
    result = _member(round_, quantity)
    if result.value is None:
        shown = f"<{fmt(result.detection_limit)} {CANONICAL_UNIT}"
        return _Fill(
            kind="result",
            text=shown,
            value=None,
            sources=(lab_source(result),),
            explanation=f"{quantity} was not detected on {long_date(round_.date)}: the lab reported "
            f"<{fmt(result.detection_limit)}, meaning below the detection limit of {fmt_ug(result.detection_limit)}. "
            "Describe it as not detected, not as a measured value.",
        )
    return _Fill(
        kind="result",
        text=fmt_ug(result.value),
        value=fmt(result.value),
        sources=(lab_source(result),),
        explanation=f"{quantity} on {long_date(round_.date)}, copied from {result.file} row {result.row}.",
    )


def _fill_lor(ds: Dataset, well: str, fields: list[str]) -> _Fill:
    quantity = _quantity(ds, fields[0])
    if quantity == SUM_KEY:
        raise EvidencelineError("a sum has no detection limit. Ask for the detection limit of PFOS or PFHxS.")
    _placeholder_well(ds, fields[1], well)
    round_ = _find_round(ds, well, fields[2])
    result = _member(round_, quantity)
    state = "not detected" if result.value is None else f"detected at {fmt_ug(result.value)}"
    return _Fill(
        kind="detection limit",
        text=fmt_ug(result.detection_limit),
        value=fmt(result.detection_limit),
        sources=(lab_source(result),),
        explanation=f"Detection limit for {quantity} on {long_date(round_.date)} ({state}), from {result.file} "
        f"row {result.row}.",
    )


def _fill_limit(ds: Dataset, fields: list[str]) -> _Fill:
    quantity = _quantity(ds, fields[0])
    if not fields[1].strip():
        raise EvidencelineError(f"name the rule ({' or '.join(ds.rules)}). Evidenceline never picks one.")
    rule = ds.resolve_rule(fields[1])
    exact = [limit for limit in rule.limits if limit.key == quantity]
    if not exact:
        if quantity == SUM_KEY:
            reason = f"rule {rule.id} has no value for the sum of PFOS and PFHxS: it screens PFOS and PFHxS separately"
        elif any(quantity in limit.members for limit in rule.limits):
            reason = (
                f"rule {rule.id} lists this value once, as the sum of PFOS and PFHxS, and it applies to {quantity} "
                f'on its own too (Table 4, footnote a: "{FOOTNOTE_A}"). Use {{limit|PFOS+PFHxS|{rule.id}}} and '
                f"say in the sentence whether {quantity} alone or the sum is compared with it"
            )
        else:
            reason = f"rule {rule.id} has no value for {quantity}. It has values for: " + ", ".join(
                limit.label for limit in rule.limits
            )
        raise EvidencelineError(reason + ".")
    limit = exact[0]
    return _Fill(
        kind="guideline limit",
        text=fmt_ug(limit.value),
        value=fmt(limit.value),
        sources=(limit_source(rule, limit),),
        explanation=f"{rule.name}: {limit.label} {fmt_ug(limit.value)} ({rule.citation}). {rule.wa_status}",
        rule=rule.id,
    )


def _signed(value: Decimal) -> str:
    return f"+{fmt(value)}" if value > 0 else fmt(value)


def _fill_change(ds: Dataset, well: str, fields: list[str]) -> _Fill:
    quantity = _quantity(ds, fields[0])
    _placeholder_well(ds, fields[1], well)
    first = _find_round(ds, well, fields[2])
    second = _find_round(ds, well, fields[3])
    if first.date == second.date:
        raise EvidencelineError(f"FROM and TO are the same round ({long_date(first.date)}).")
    if first.date > second.date:
        raise EvidencelineError(
            f"FROM ({long_date(first.date)}) is later than TO ({long_date(second.date)}). "
            "Write the earlier round first."
        )
    before, before_rows = _detected_value(first, quantity)
    after, after_rows = _detected_value(second, quantity)
    diff = after - before
    pct = percent_change(before, after)
    label = "Sum of PFOS and PFHxS" if quantity == SUM_KEY else quantity
    if diff == 0:
        text = f"no change ({fmt_ug(before)} in both rounds)"
    else:
        size = fmt_ug(abs(diff)) + ("" if pct is None else f" ({format(abs(pct), 'f')}%)")
        text = f"{'a rise' if diff > 0 else 'a fall'} of {size}"
    pct_part = "" if pct is None else f" ({format(pct, '+f') if pct > 0 else format(pct, 'f')}%)"
    difference = SourceRef(
        kind="computed difference",
        reference=f"computed by code: {label} {fmt(after)} on {long_date(second.date)} minus {fmt(before)} on "
        f"{long_date(first.date)} = {_signed(diff)} {CANONICAL_UNIT}{pct_part}",
        value=fmt(abs(diff)),
        analyte=quantity,
    )
    sources: list[SourceRef] = [difference]
    for round_, rows, total in ((first, before_rows, before), (second, after_rows, after)):
        if quantity == SUM_KEY:
            sources.append(_sum_source(round_, rows, total))
        sources.extend(lab_source(r) for r in rows)
    return _Fill(
        kind="change",
        text=text,
        value=_signed(diff),
        sources=tuple(sources),
        explanation=f"{label} went from {fmt_ug(before)} on {long_date(first.date)} to {fmt_ug(after)} on "
        f"{long_date(second.date)}: {text}, computed by code. The percent is rounded to one decimal place.",
    )


def _resolve(ds: Dataset, well: str, body: str) -> _Fill:
    fields = [f.strip() for f in body.split("|")]
    head = fields[0].lower()
    expected = {"limit": 3, "lor": 4, "change": 5}.get(head, 3)
    if len(fields) != expected:
        form = head if head in ("limit", "lor", "change") else "result"
        raise EvidencelineError(
            f"a {form} placeholder has {expected} fields separated by '|', this one has {len(fields)}."
        )
    if head == "limit":
        return _fill_limit(ds, fields[1:])
    if head == "lor":
        return _fill_lor(ds, well, fields[1:])
    if head == "change":
        return _fill_change(ds, well, fields[1:])
    return _fill_result(ds, well, fields)


# --- the public function -------------------------------------------------------------------------------------------


def _form_help() -> str:
    return "Valid forms:\n" + "\n".join(f"- {form}" for form in VALID_FORMS) + f"\n{_DATE_FORMS}"


MAX_PROBLEMS_SHOWN = 10
"""An error lists at most this many problems, so a text full of stray braces cannot produce a huge message."""
_CLIP_CHARS = 80


def _clip(text: str) -> str:
    """``text`` cut to a readable length for an error message."""
    return text if len(text) <= _CLIP_CHARS else text[: _CLIP_CHARS - 4] + " ..."


def _stray_braces(text: str) -> list[str]:
    remaining = _PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), text)
    return [
        f"unmatched {ch!r} at character {i + 1}, near {text[max(0, i - 15) : i + 25]!r}"
        for i, ch in enumerate(remaining)
        if ch in "{}"
    ]


def _notes(fills: list[_Fill], ds: Dataset) -> list[str]:
    kinds = {f.kind for f in fills}
    notes: list[str] = []
    if kinds & {"result", "detection limit", "sum", "change"}:
        notes.append("Lab values come from synthetic data for a fictional site (FDS-01).")
    rules_used = {f.rule for f in fills if f.rule is not None}
    if rules_used:
        if len(rules_used) < len(ds.rules):
            others = ", ".join(r for r in ds.rules if r not in rules_used)
            notes.append(
                f"Only the {', '.join(sorted(rules_used))} guideline values are quoted. {RULE_CHOICE} "
                f"compare_rules shows {others} alongside."
            )
        else:
            notes.append(RULE_CHOICE)
        notes.extend([EQUAL_IS_NOT_ABOVE, INVESTIGATION_LEVEL])
    if "change" in kinds:
        notes.append(
            "Percent changes are computed by code, but check_paragraph does not trace percentages: it lists them "
            "as not checked. Their working is in the numbered sources."
        )
    return notes


def fill_placeholders(text: str, well: str, *, data: Dataset | None = None) -> FilledText:
    """Replace every placeholder in ``text`` with its exact value and unit. Raises EvidencelineError on any bad one.

    Nothing is filled unless every placeholder resolves, so a half-filled paragraph never comes back.
    """
    ds = data if data is not None else default_dataset()
    if not text.strip():
        raise EvidencelineError("The text is empty. Write the paragraph with placeholders, then call this tool.")
    well_id = ds.resolve_well(well)
    problems = _stray_braces(text)
    matches = list(_PLACEHOLDER.finditer(text))
    if not matches and not problems:
        raise EvidencelineError(
            "No placeholders found. Write each number as a placeholder instead of typing it.\n" + _form_help()
        )
    fills: list[_Fill] = []
    for index, match in enumerate(matches, start=1):
        try:
            fills.append(_resolve(ds, well_id, match.group(1)))
        except EvidencelineError as exc:
            problems.append(f"placeholder {index} {_clip(match.group(0))}: {exc}")
    if problems:
        shown = problems[:MAX_PROBLEMS_SHOWN]
        listed = "\n".join(f"- {p}" for p in shown)
        if len(problems) > len(shown):
            listed += f"\n- and {len(problems) - len(shown)} more problems of the same kinds, not listed"
        raise EvidencelineError(f"Nothing was filled. Fix these placeholders:\n{listed}\n{_form_help()}")

    numbered: dict[tuple[str, str], NumberedSource] = {}
    values: list[FilledValue] = []
    plain: list[str] = []
    annotated: list[str] = []
    cursor = 0
    length = 0
    for index, (match, fill) in enumerate(zip(matches, fills, strict=True), start=1):
        numbers: list[int] = []
        for ref in fill.sources:
            key = (ref.kind, ref.reference)
            if key not in numbered:
                numbered[key] = NumberedSource(number=len(numbered) + 1, **ref.model_dump())
            numbers.append(numbered[key].number)
        before = text[cursor : match.start()]
        plain.append(before)
        annotated.append(before)
        start = length + len(before)
        plain.append(fill.text)
        annotated.append(_annotate(fill.text, numbers))
        length = start + len(fill.text)
        cursor = match.end()
        values.append(
            FilledValue(
                number=index,
                placeholder=match.group(0),
                kind=fill.kind,
                text=fill.text,
                value=fill.value,
                start=start,
                end=length,
                rule=fill.rule,
                source_numbers=numbers,
                explanation=fill.explanation,
            )
        )
    plain.append(text[cursor:])
    annotated.append(text[cursor:])
    return FilledText(
        well=well_id,
        text="".join(plain),
        annotated_text="".join(annotated),
        values=values,
        sources=list(numbered.values()),
        next_step=(
            f"Call check_paragraph with this text and well {well_id!r} before using it, and show the person every "
            "item that is not consistent and every not-checked item. Filling the numbers does not check the words "
            "around them, such as 'above', 'fell' or 'not detected'."
        ),
        notes=_notes(fills, ds),
    )


def _annotate(value_text: str, numbers: list[int]) -> str:
    return f"{value_text} [{', '.join(str(n) for n in numbers)}]"


def redact_filled(result: FilledText, redact: Callable[[Any], Any]) -> FilledText:
    """Redact a fill result without letting redaction reach into a filled value.

    The words between the values are the caller's own paragraph; the values are numbers from the packaged data.
    Redacting the joined text lets a pattern straddle the join: 'Lot ' + '0.038 ug/L' reads as a land lot, which
    mangles the number and shifts every offset after it. So each stretch of the caller's words is redacted on its
    own, the values go back between them unchanged, and the offsets are worked out again.
    """
    edges = [0, *(edge for value in result.values for edge in (value.start, value.end)), len(result.text)]
    gaps = [cast(str, redact(result.text[edges[i] : edges[i + 1]])) for i in range(0, len(edges), 2)]
    rest = cast(FilledText, redact(result.model_copy(update={"text": "", "annotated_text": "", "values": []})))
    plain = [gaps[0]]
    annotated = [gaps[0]]
    values: list[FilledValue] = []
    length = len(gaps[0])
    for value, gap in zip(result.values, gaps[1:], strict=True):
        meta = cast(FilledValue, redact(value.model_copy(update={"text": ""})))
        start = length
        length += len(value.text)
        values.append(meta.model_copy(update={"text": value.text, "start": start, "end": length}))
        plain += [value.text, gap]
        annotated += [_annotate(value.text, value.source_numbers), gap]
        length += len(gap)
    return rest.model_copy(update={"text": "".join(plain), "annotated_text": "".join(annotated), "values": values})


# --- MCP tool ------------------------------------------------------------------------------------------------------


def fill_numbers(
    text: Annotated[
        str,
        Field(
            description="The draft paragraph, with every number written as a placeholder such as {PFOS|MB2|Sep 2025}."
        ),
    ],
    well: Annotated[str, Field(description="Monitoring well id, for example 'MB2'. Placeholders must name this well.")],
) -> FilledText:
    """Fill in the numbers: replace each placeholder in a draft paragraph with the exact value and unit, by code.

    Never type a concentration yourself. Write a placeholder where each number goes, then call this tool. Fields
    are separated by '|'; case and spaces around fields do not matter:
    - {ANALYTE|WELL|DATE}: measured result, e.g. {PFOS|MB2|Sep 2025} gives '0.038 ug/L'. A non-detect gives the
      result as reported, e.g. '<0.001 ug/L'; describe it as not detected.
    - {sum PFOS+PFHxS|WELL|DATE}: the sum of PFOS and PFHxS, e.g. {sum PFOS+PFHxS|MB2|2025-09-16} gives '0.057 ug/L'.
    - {lor|ANALYTE|WELL|DATE}: the detection limit, e.g. {lor|PFOA|MB2|Sep 2025} gives '0.001 ug/L'.
    - {limit|ANALYTE|RULE}: a guideline value. RULE is 'nemp-3.0' or 'current' and is required, e.g.
      {limit|PFOS|current} or {limit|PFOS+PFHxS|nemp-3.0}. Under nemp-3.0 use the PFOS+PFHxS form: its 0.07 ug/L
      applies to PFOS alone, PFHxS alone and the sum (Table 4, footnote a), so say which one the sentence compares.
    - {change|ANALYTE|WELL|FROM|TO}: change from the earlier round FROM to the later round TO, with direction and
      percent, e.g. {change|PFOS|MB2|Nov 2024|Sep 2025} gives 'a fall of 0.003 ug/L (7.3%)'. Write the sentence so
      the phrase fits, e.g. 'PFOS showed {change|...} between November 2024 and September 2025.'
    Dates: '2025-09-16', '16 September 2025' or 'Sep 2025'. One well per call.

    Returns the filled text, the same text with [n] source markers, each value with its offsets and explanation,
    and a numbered source list (lab file and row, or guideline document, table and page). If any placeholder is
    unknown or ambiguous, nothing is filled and the error lists every problem and the valid forms.
    Afterwards, call check_paragraph on the filled text: it checks the words around the numbers.
    """
    try:
        return fill_placeholders(text, well)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

TOOL_SPECS: tuple[tuple[Callable[..., BaseModel], str], ...] = ((fill_numbers, "Fill in the numbers"),)
"""(function, title) pairs, for a server that builds its tool list itself (for example with strict arguments)."""


def register_tools(server: MCPServer) -> None:
    """Add this module's tools to ``server``."""
    for fn, title in TOOL_SPECS:
        server.add_tool(fn, title=title, annotations=READ_ONLY)
