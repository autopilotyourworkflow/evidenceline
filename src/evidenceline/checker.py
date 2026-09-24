"""Deterministic paragraph checker. No language model is involved.

For each sentence it finds:

(a) numbers with a concentration unit, and traces each one to a lab value, a detection limit, a sum or
    difference computed from lab values, or a guideline limit;
(b) claims about change between two dates (increased, fell, lower than, stable, ...), verified against the data;
(c) claims that a result is above, below or within a guideline (or a stated number), evaluated under each rule;
(d) claims that an analyte was or was not detected.

Negation ("did not increase") and common date forms ("Sept 2025", "16 September 2025") are handled. Anything it
cannot read confidently is listed under ``not_checked`` with the reason. It never reports "no issues found".
"""

from __future__ import annotations

import bisect
import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from evidenceline.dataset import SUM_KEY, SUM_MEMBERS, Dataset, LabResult, Round, Rule, long_date
from evidenceline.errors import EvidencelineError
from evidenceline.models import (
    CheckedItem,
    CheckStatus,
    NotCheckedItem,
    ParagraphCheck,
    RuleOutcome,
    SourceKind,
    SourceRef,
)
from evidenceline.screening import (
    NEGATED,
    RULE_CHOICE,
    Op,
    compare_bounds,
    lab_source,
    limit_source,
    limits_for_analyte,
    member_of_sum_note,
    screen_limit,
    screen_member,
)
from evidenceline.textparse import (
    AnalyteMention,
    DateMention,
    NumberMention,
    SpanSet,
    find_analytes,
    find_dates,
    find_numbers,
    find_rules,
    find_well_ids,
    find_years,
    split_sentences,
)
from evidenceline.units import fmt, fmt_ug, percent_change

SCOPE = (
    "Checks four kinds of statement: numbers with a concentration unit (ug/L, ng/L, mg/L), change between two "
    "dates, comparisons with a drinking-water guideline or a stated number, and whether an analyte was detected. "
    "Anything else in the paragraph (interpretation, causes, recommendations) is not checked."
)

Direction = Literal["up", "down", "same", "changed"]
Kind = Literal["number", "change", "guideline", "detection"]

# --- trigger patterns --------------------------------------------------------------------------------------------

_COMPARISON = re.compile(
    r"\b(?P<le>at\s+or\s+below|within|meets?|met|compl(?:y|ies|ied)\s+with)\b"
    r"|\b(?P<gt>above|exceed(?:s|ed|ing)?|exceedances?|in\s+excess\s+of|over|higher\s+than|greater\s+than|more\s+than)\b"
    r"|\b(?P<lt>below|under|lower\s+than|less\s+than)\b",
    re.IGNORECASE,
)
# "detection" inside "detection limit" or "limit of detection" names a limit; it is not a claim of detection.
_DETECTION = re.compile(
    r"\b(?P<neg>non-?detects?|undetected)\b|(?<!\bof\s)\b(?P<pos>detected|detections?)\b(?!\s+limits?\b)",
    re.IGNORECASE,
)
_UP = r"increas(?:e|ed|es|ing)|rose|risen|rises?|rising|went\s+up|gone\s+up|goes\s+up|up\s+from|climbed|higher"
_DOWN = (
    r"decreas(?:e|ed|es|ing)|fell|fallen|falls?|falling|went\s+down|gone\s+down|goes\s+down|down\s+from|lower"
    r"|dropped|drops?"
    r"|dropping|declin(?:e|ed|es|ing)"
)
_SAME = r"similar|stable|unchanged|steady"
_CHANGED = r"chang(?:e|ed|es)"
_CHANGE = re.compile(
    rf"\b(?:(?P<up>{_UP})|(?P<down>{_DOWN})|(?P<same>{_SAME})|(?P<changed>{_CHANGED}))\b", re.IGNORECASE
)
_COMPARATIVE = re.compile(r"^(?:higher|lower)$", re.IGNORECASE)

_LOR_TARGET = re.compile(
    r"\b(?:detection\s+limits?|limits?\s+of\s+(?:reporting|detection)|reporting\s+limits?|LOR|LOD)\b", re.IGNORECASE
)
_OTHER_SCENARIO = re.compile(
    r"\b(?:recreational|fresh\s*water|marine|ecolog\w*|irrigation|non-potable|soil|species\s+protection|biota)\b",
    re.IGNORECASE,
)
_GUIDELINE_TARGET = re.compile(
    r"\b(?:guidelines?|limits?|criteri(?:on|a)|screening\s+(?:levels?|values?)|investigation\s+levels?"
    r"|trigger\s+values?|ADWG|NEMP|NHMRC|standards?)\b",
    re.IGNORECASE,
)
_TARGET_TAIL = re.compile(
    r"(?:\s*(?:v(?:ersion)?\.?\s*)?3\.[01]\b)?(?:\s*\(\s*\d{4}\s*\))?"
    r"(?:\s+(?:drinking[- ]water|guidelines?|values?|levels?|limits?|criteri(?:on|a)))*",
    re.IGNORECASE,
)
_RULE_REFERENCE_AFTER_UNDER = re.compile(
    r"\s*(?:the\s+|both\s+|either\s+)?(?:current\b|NEMP|ADWG|NHMRC|rules?\b|editions?\b|guidelines?\s+(?:edition|version))",
    re.IGNORECASE,
)
_WINDOW_END = re.compile(r"[;,.]\s|[;,]|\.$|\b(?:but|while|whereas|although)\b", re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r"[;,:]|\b(?:and|but|while|whereas|although|though)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|nor|neither)\b|n't\b", re.IGNORECASE)
_NOW = re.compile(
    r"\b(?:now|currently|at\s+present|presently|latest|most\s+recent(?:ly)?|remains|still)\b", re.IGNORECASE
)
_ALL_ROUNDS = re.compile(
    r"\b(?:any|all|every|each)\s+(?:of\s+the\s+)?(?:monitoring\s+|sampling\s+)?(?:rounds?|events?|occasions?)\b",
    re.IGNORECASE,
)
_QUALIFIER = re.compile(
    r"\b(slightly|marginally|significantly|substantially|sharply|dramatically|considerably|greatly|markedly)\b",
    re.IGNORECASE,
)
_REFERENCE_BEFORE_DATE = re.compile(
    r"\b(?:than|compared\s+(?:with|to)|relative\s+to|versus|vs\.?|since)\s+(?:(?:in|on|the|at)\s+)?$", re.IGNORECASE
)
_REFERENCE_BEFORE_VALUE = re.compile(
    r"\b(?:than|compared\s+(?:with|to)|relative\s+to|versus|vs\.?|from)\s+(?:(?:about|around|the|a)\s+)?[<(]?\s*$",
    re.IGNORECASE,
)
_TRAILING_FILLER = re.compile(r"\s+(?:in|on|at|of|for|the|by)$", re.IGNORECASE)
_REF_MARKER = re.compile(r"\b(?:than|compared\s+(?:with|to)|relative\s+to|versus|vs\.?)\s", re.IGNORECASE)
_SINCE_BEFORE_DATE = re.compile(r"\bsince\s+(?:[\w-]+\s+){0,4}$", re.IGNORECASE)
_LOOKBEHIND = 80
"""Characters before a mention that the "what comes right before" patterns need to see."""

# What a quoted number is, read from the words right before it. A number with none of these is read as a
# measured result of the analyte the sentence is about ("PFOS was 0.038 ug/L", "ranged from 0.006 to 0.062").
_THRESHOLD_BEFORE_VALUE = re.compile(
    r"\b(?:at\s+or\s+below|within|meets?|met|compl(?:y|ies|ied)\s+with|above|exceed(?:s|ed|ing)?|in\s+excess\s+of"
    r"|over|higher\s+than|greater\s+than|more\s+than|below|under|lower\s+than|less\s+than|against)"
    # "below the current value of 0.03 ug/L": the rule's name can sit before "value".
    r"\s+(?:the\s+)?(?:current\s+(?:drinking[- ]water\s+)?)?(?:(?:value|level|concentration)\s+of\s+)?$",
    re.IGNORECASE,
)
_LOR_BEFORE_VALUE = re.compile(
    r"\b(?:(?:detection|reporting)\s+limits?|limits?\s+of\s+(?:reporting|detection)|LOR|LOD)"
    r"(?:\s*\(\s*(?:LOR|LOD)\s*\))?\s*(?:(?:of|is|was|were|at|=|:)\s*)?\(?\s*$",
    re.IGNORECASE,
)
_GUIDELINE_BEFORE_VALUE = re.compile(
    r"\b(?:guidelines?|limits?|criteri(?:on|a)|screening\s+(?:levels?|values?)|investigation\s+levels?"
    r"|trigger\s+values?|standards?|ADWG|NHMRC|NEMP(?:\s*(?:v(?:ersion)?\.?\s*)?3(?:\.[01])?)?)"
    r"(?:\s*\(\s*\d{4}\s*\))?(?:\s+(?:drinking[- ]water|guidelines?|values?|levels?|limits?|criteri(?:on|a)))*"
    r"(?:\s+for\s+[^,;:()]{1,45}?)?"  # "the ADWG value for PFOS is", "the criterion for the sum of PFOS and PFHxS is"
    r"\s*(?:(?:of|is|was|are|were|at|=|:|equals?|set\s+at)\s*)?(?:about\s+)?\(?\s*$",
    re.IGNORECASE,
)
_DIFFERENCE_BEFORE_VALUE = re.compile(
    r"(?:\bby|\b(?:increase|decrease|rise|fall|drop|decline|reduction|change|difference)s?\s+of)"
    r"\s+(?:about\s+|around\s+|approximately\s+|roughly\s+|some\s+)?$",
    re.IGNORECASE,
)
_BARE_SUM = re.compile(r"\b(?:sum|total|combined)\b", re.IGNORECASE)

# Judgements the data cannot support on its own: a guideline value is an investigation level, not a finding.
_JUDGEMENT = re.compile(
    r"\b(?:contaminated|polluted|unsafe|hazardous|toxic|safe\s+(?:to|for)\s+\w+|(?:health|human|ecological)\s+risks?"
    r"|poses?\s+(?:an?\s+)?(?:\w+\s+)?risks?)\b",
    re.IGNORECASE,
)
_JUDGEMENT_REASON = (
    "This is a judgement about contamination, safety or risk. Lab results and guideline values cannot support it "
    "on their own: a guideline value is an investigation level, not a finding. A person must decide."
)

NumberRole = Literal["result", "guideline", "detection limit", "difference", "threshold"]
_ROLE_SOURCES: dict[NumberRole, tuple[SourceKind, ...]] = {
    "result": ("lab result", "detection limit", "computed sum"),
    "guideline": ("guideline limit",),
    "detection limit": ("detection limit",),
    "difference": ("computed difference",),
    "threshold": ("lab result", "detection limit", "computed sum", "computed difference", "guideline limit"),
}
_ROLE_WORDS: dict[NumberRole, str] = {
    "result": "a measured result",
    "guideline": "a guideline value",
    "detection limit": "a detection limit",
    "difference": "a change between two rounds",
    "threshold": "a value to compare against",
}


# --- traceable values ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Traceable:
    value: Decimal
    source: SourceRef
    analytes: frozenset[str]
    dates: frozenset[dt.date]
    """Dates the value belongs to. Empty for guideline limits, which do not depend on the date."""
    is_detection_limit: bool = False


@dataclass(frozen=True, slots=True)
class _Sum:
    total: Decimal
    pfos: Decimal
    pfhxs: Decimal
    rows: tuple[LabResult, LabResult]


def _sum_value(round_: Round) -> _Sum | None:
    first, second = (round_.results.get(name) for name in SUM_MEMBERS)
    if first is None or second is None or first.value is None or second.value is None:
        return None
    return _Sum(first.value + second.value, first.value, second.value, (first, second))


def _sum_source(round_: Round, summed: _Sum) -> SourceRef:
    first, second = summed.rows
    return SourceRef(
        kind="computed sum",
        reference=f"computed by code: PFOS {fmt(summed.pfos)} + PFHxS {fmt(summed.pfhxs)} = "
        f"{fmt_ug(summed.total)} on {long_date(round_.date)} ({round_.lab_report}, {round_.file} "
        f"rows {first.row} + {second.row})",
        value=fmt(summed.total),
        analyte=SUM_KEY,
        date=round_.date.isoformat(),
        lab_report=round_.lab_report,
        file=round_.file,
        rows=[first.row, second.row],
    )


def _result_source(round_: Round, analyte: str, value: Decimal, *, less_than: bool) -> SourceRef | None:
    """The source when ``analyte`` (or the sum key) in ``round_`` is exactly what a sentence states.

    ``less_than`` means the sentence wrote "<value": the analyte must be a non-detect with that detection limit.
    """
    if analyte == SUM_KEY:
        summed = _sum_value(round_)
        if less_than or summed is None or summed.total != value:
            return None
        return _sum_source(round_, summed)
    row = round_.results.get(analyte)
    if row is None:
        return None
    if less_than:
        return lab_source(row) if row.value is None and row.detection_limit == value else None
    return lab_source(row) if row.value is not None and row.value == value else None


def _value_of(round_: Round, analyte: str) -> tuple[Decimal | None, list[LabResult]]:
    """The exact value for an analyte or the sum key in a round, with the rows it came from."""
    if analyte == SUM_KEY:
        summed = _sum_value(round_)
        if summed is None:
            return None, [r for r in (round_.results.get(n) for n in SUM_MEMBERS) if r is not None]
        return summed.total, list(summed.rows)
    result = round_.results.get(analyte)
    if result is None:
        return None, []
    return result.value, [result]


def _describe_value(round_: Round, analyte: str) -> str:
    value, rows = _value_of(round_, analyte)
    where = (
        f"{round_.lab_report}, {round_.file} row{'s' if len(rows) > 1 else ''} {' + '.join(str(r.row) for r in rows)}"
    )
    if analyte == SUM_KEY and value is not None:
        parts = " + ".join(fmt(r.value) for r in rows if r.value is not None)
        return f"sum of PFOS and PFHxS {parts} = {fmt_ug(value)} on {long_date(round_.date)} ({where})"
    shown = fmt_ug(value) if value is not None else "not detected"
    return f"{_label(analyte)} {shown} on {long_date(round_.date)} ({where})"


def _label(analyte: str) -> str:
    return "the sum of PFOS and PFHxS" if analyte == SUM_KEY else analyte


def _build_traceables(rounds: Sequence[Round], rules: Sequence[Rule]) -> list[_Traceable]:
    items: list[_Traceable] = []
    for round_ in rounds:
        by_lor: dict[Decimal, list[LabResult]] = {}
        for result in round_.results.values():
            if result.value is not None:
                items.append(
                    _Traceable(result.value, lab_source(result), frozenset({result.analyte}), frozenset({round_.date}))
                )
            by_lor.setdefault(result.detection_limit, []).append(result)
        for lor, rows in by_lor.items():
            items.append(
                _Traceable(
                    lor,
                    SourceRef(
                        kind="detection limit",
                        reference=f"detection limit {fmt_ug(lor)} for {len(rows)} analytes on {long_date(round_.date)} "
                        f"({round_.lab_report}, {round_.file})",
                        value=fmt(lor),
                        date=round_.date.isoformat(),
                        lab_report=round_.lab_report,
                        file=round_.file,
                        rows=sorted(r.row for r in rows),
                    ),
                    frozenset(r.analyte for r in rows),
                    frozenset({round_.date}),
                    is_detection_limit=True,
                )
            )
        summed = _sum_value(round_)
        if summed is not None:
            items.append(
                _Traceable(
                    summed.total,
                    _sum_source(round_, summed),
                    frozenset({SUM_KEY, *SUM_MEMBERS}),
                    frozenset({round_.date}),
                )
            )
    for i, earlier in enumerate(rounds):
        for later in rounds[i + 1 :]:
            for analyte in (*SUM_MEMBERS, SUM_KEY):
                before, _ = _value_of(earlier, analyte)
                after, _ = _value_of(later, analyte)
                if before is None or after is None or before == after:
                    continue
                diff = abs(after - before)
                items.append(
                    _Traceable(
                        diff,
                        SourceRef(
                            kind="computed difference",
                            reference=f"computed by code: {_label(analyte)} {fmt(after)} on {long_date(later.date)} "
                            f"minus {fmt(before)} on {long_date(earlier.date)} = "
                            f"{'+' if after > before else '-'}{fmt_ug(diff)}",
                            value=fmt(diff),
                            analyte=analyte,
                        ),
                        frozenset({analyte, *(SUM_MEMBERS if analyte == SUM_KEY else ())}),
                        frozenset({earlier.date, later.date}),
                    )
                )
    for rule in rules:
        for limit in rule.limits:
            items.append(
                _Traceable(
                    limit.value,
                    limit_source(rule, limit),
                    frozenset({limit.key, *limit.members}),
                    frozenset(),
                )
            )
    return items


# --- per-sentence parse -------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Sentence:
    index: int
    text: str
    rules: set[str]
    dates: list[DateMention]
    analytes: list[AnalyteMention]
    numbers: list[NumberMention]
    unclear: list[str]
    wells: list[str]
    break_starts: list[int]
    break_ends: list[int]
    """Clause breaks, worked out once per sentence so long sentences stay fast."""
    reference_dates: frozenset[int]
    """Start positions of dates that name the round compared against ("higher than in November 2024")."""
    claimed: SpanSet = field(default_factory=SpanSet)
    implied: list[Round] = field(default_factory=list[Round])
    """Rounds pinned down by lab values quoted in the sentence, used when the sentence gives no date."""


@dataclass(frozen=True, slots=True)
class _Resolved:
    rounds: list[Round]
    problem: str | None = None
    note: str | None = None
    read_as_now: bool = False
    """True when a word such as "now" picked the latest round."""


class _ParagraphChecker:
    def __init__(self, data: Dataset, well: str) -> None:
        self.data = data
        self.well = data.resolve_well(well)
        self.rounds = data.rounds(self.well)
        self.rules = list(data.rules.values())
        self.traceables = _build_traceables(self.rounds, self.rules)
        self.checked: list[CheckedItem] = []
        self.not_checked: list[NotCheckedItem] = []

    # --- driver -----------------------------------------------------------------------------------------------

    def run(self, text: str) -> ParagraphCheck:
        sentences = split_sentences(text)
        if not sentences:
            raise EvidencelineError("The paragraph is empty. Pass the text you want checked.")
        for index, sentence_text in enumerate(sentences, start=1):
            self._check_sentence(self._parse(index, sentence_text))
        return self._report(sentences)

    def _parse(self, index: int, text: str) -> _Sentence:
        used = SpanSet()
        rules = {r.rule for r in find_rules(text, used)}
        dates, unclear_dates = find_dates(text, used)
        years = find_years(text, used)
        analytes = find_analytes(text, self.data.analytes(), used)
        numbers = find_numbers(text, used)
        unclear = [s.text for s in unclear_dates] + [s.text for s in years]
        break_starts: list[int] = []
        break_ends: list[int] = []
        date_starts = {d.start for d in dates}
        date_ends = {d.end for d in dates}
        for m in _CLAUSE_BREAK.finditer(text):
            # An "and" joining two dates ("between March 2024 and May 2026") does not end a clause.
            if m.group(0).lower() == "and" and _near(date_ends, m.start(), before=True) and _near(date_starts, m.end()):
                continue
            break_starts.append(m.start())
            break_ends.append(m.end())
        references = frozenset(
            d.start for d in dates if _REFERENCE_BEFORE_DATE.search(text[max(0, d.start - _LOOKBEHIND) : d.start])
        )
        return _Sentence(
            index,
            text,
            rules,
            dates,
            analytes,
            numbers,
            unclear,
            find_well_ids(text),
            break_starts,
            break_ends,
            references,
        )

    def _skip(self, sentence: _Sentence, quote: str, reason: str) -> None:
        self.not_checked.append(NotCheckedItem(sentence=sentence.index, quote=self._tidy(quote), reason=reason))

    def _tidy(self, quote: str) -> str:
        quote = quote.strip()
        while (m := _TRAILING_FILLER.search(quote)) is not None:
            quote = quote[: m.start()]
        return quote

    def _add(
        self, sentence: _Sentence, quote: str, kind: Kind, status: CheckStatus, explanation: str, **extra: object
    ) -> None:
        self.checked.append(
            CheckedItem.model_validate(
                {
                    "sentence": sentence.index,
                    "quote": self._tidy(quote),
                    "kind": kind,
                    "status": status,
                    "explanation": explanation,
                }
                | extra
            )
        )

    def _check_sentence(self, s: _Sentence) -> None:
        before = (len(self.checked), len(self.not_checked))
        other_wells = [w for w in s.wells if w != self.well.upper()]
        if other_wells:
            self._skip(
                s,
                s.text,
                f"This sentence names well {', '.join(other_wells)}. This check only has data for {self.well}.",
            )
            return
        for unclear in s.unclear:
            if unclear.isdigit():
                reason = "A year on its own is not used to pick a monitoring round. Give the month, such as 'Sep 2025'."
            elif any(ch.isalpha() for ch in unclear):
                reason = "This date does not exist on the calendar, so it was not used."
            else:
                reason = "Numeric dates such as 05/06/2025 can be read two ways. Write the month as a word."
            self._skip(s, unclear, reason)
        self._check_numbers(s)
        self._check_detection_claims(s)
        self._check_comparison_claims(s)
        self._check_change_claims(s)
        for m in _JUDGEMENT.finditer(s.text):
            self._skip(s, m.group(0), _JUDGEMENT_REASON)
        if (len(self.checked), len(self.not_checked)) == before:
            self._skip(s, s.text, "No numbers with a unit, and no change, guideline or detection claims were found.")

    # --- (a) numbers --------------------------------------------------------------------------------------------

    def _clause_bounds(self, s: _Sentence, pos: int) -> tuple[int, int]:
        """Start and end of the clause around ``pos``. An "and" joining two dates does not end a clause."""
        # Breaks never overlap, so both lists are sorted and a binary search finds the neighbours.
        before = bisect.bisect_right(s.break_ends, pos)
        start = s.break_ends[before - 1] if before else 0
        after = bisect.bisect_left(s.break_starts, pos)
        end = s.break_starts[after] if after < len(s.break_starts) else len(s.text)
        return start, end

    def _is_reference(self, s: _Sentence, mention: DateMention) -> bool:
        return mention.start in s.reference_dates

    def _dates_for(self, s: _Sentence, pos: int) -> list[DateMention]:
        """Dates that a claim or number at ``pos`` refers to.

        Dates in the same clause come first. Otherwise the sentence's dates are used, leaving out reference dates
        such as the one in "higher than in November 2024", which name the round being compared against.
        """
        start, end = self._clause_bounds(s, pos)
        local = [d for d in s.dates if d.start >= start and d.end <= end]
        if local:
            return local
        return [d for d in s.dates if not self._is_reference(s, d)]

    def _head(self, s: _Sentence, pos: int) -> str:
        """The text just before ``pos``: enough for the patterns that read what comes right before a mention."""
        return s.text[max(0, pos - _LOOKBEHIND) : pos]

    def _is_since(self, s: _Sentence, mention: DateMention) -> bool:
        return _SINCE_BEFORE_DATE.search(self._head(s, mention.start)) is not None

    def _number_role(self, s: _Sentence, number: NumberMention) -> NumberRole:
        """What a quoted number is, read from the words right before it. The default is a measured result."""
        head = self._head(s, number.start)
        if _THRESHOLD_BEFORE_VALUE.search(head):
            return "threshold"
        if _LOR_BEFORE_VALUE.search(head):
            return "detection limit"
        if _GUIDELINE_BEFORE_VALUE.search(head):
            return "guideline"
        if _DIFFERENCE_BEFORE_VALUE.search(head):
            return "difference"
        return "result"

    def _number_analytes(self, s: _Sentence, number: NumberMention) -> tuple[str, ...] | str:
        """The analyte a quoted number belongs to, or the reason it cannot be told.

        The subject is the nearest analyte named before the number ("PFOS was 0.038 ug/L; PFHxS was 0.019 ug/L"),
        or the first one after it when none comes before ("0.038 ug/L of PFOS"). "The sum was ..." names the sum.
        """
        before = [a for a in s.analytes if a.end <= number.start]
        mention = before[-1] if before else next((a for a in s.analytes if a.start >= number.end), None)
        gap_start = mention.end if mention is not None and mention.end <= number.start else 0
        if _BARE_SUM.search(s.text[max(gap_start, number.start - _LOOKBEHIND) : number.start]):
            return (SUM_KEY,)
        if mention is None:
            return "No analyte is named for this number, so it cannot be matched to a lab result."
        if mention.is_group_word:
            return "'PFAS' names a group of chemicals. Name the compound (for example PFOS) so it can be checked."
        if len(mention.analytes) > 1:
            return (
                f"The sentence names {' and '.join(mention.analytes)} together, so it is not clear which one this "
                "number belongs to. Give each value its own analyte, for example 'PFOS was 0.038 ug/L and PFHxS was "
                "0.019 ug/L'."
            )
        return mention.analytes

    def _number_scope(self, s: _Sentence, number: NumberMention) -> _Resolved:
        """The rounds a quoted result belongs to. No rounds and no problem means the sentence gives no date."""
        start, end = self._clause_bounds(s, number.start)
        # The date after "since" is where a change started, not the date of the value.
        mentions = [d for d in s.dates if d.start >= start and d.end <= end and not self._is_since(s, d)]
        if not mentions and not _REFERENCE_BEFORE_VALUE.search(self._head(s, number.start)):
            written = [d for d in s.dates if not self._is_reference(s, d) and not self._is_since(s, d)]
            now = _NOW.search(s.text[start:end]) or (None if written else _NOW.search(s.text))
            if now is not None:
                latest = self.rounds[-1]
                return _Resolved(
                    [latest],
                    note=f"'{now.group(0).capitalize()}' was read as the latest round with data, "
                    f"{long_date(latest.date)}.",
                    read_as_now=True,
                )
            mentions = written
        rounds: dict[dt.date, Round] = {}
        for mention in mentions:
            found = mention.resolve(self.rounds)
            if not found:
                available = ", ".join(long_date(r.date) for r in self.rounds)
                return _Resolved(
                    [],
                    problem=f"There is no {self.well} sampling round in {mention.label()}. Rounds with data: "
                    f"{available}.",
                )
            rounds.update((r.date, r) for r in found)
        return _Resolved(sorted(rounds.values(), key=lambda r: r.date))

    def _shown(self, number: NumberMention) -> str:
        """The number in ug/L, with the text as written when the unit was different."""
        assert number.value_ug is not None
        shown = ("<" if number.less_than else "") + fmt_ug(number.value_ug)
        written = number.text.strip()
        return shown if written.lower().replace(" ", "") == shown.lower().replace(" ", "") else f"{shown} ({written})"

    def _check_numbers(self, s: _Sentence) -> None:
        unitless = [n.text.strip() for n in s.numbers if n.value_ug is None]
        if unitless:
            self._skip(
                s,
                ", ".join(unitless),
                "Numbers without a concentration unit (ug/L, ng/L or mg/L) are not checked.",
            )
        plans: list[tuple[NumberMention, NumberRole, tuple[str, ...] | str]] = []
        for number in s.numbers:
            if number.value_ug is None:
                continue
            role = self._number_role(s, number)
            analytes = self._number_analytes(s, number)
            plans.append((number, role, analytes))
            if role == "result" and isinstance(analytes, tuple) and not number.less_than:
                value = number.value_ug
                owners = {
                    r.date: r for r in self.rounds for a in analytes if _result_source(r, a, value, less_than=False)
                }
                if len(owners) == 1:
                    s.implied.extend(owners.values())
        for number, role, analytes in plans:
            if number.negative and role != "difference":
                self._add(
                    s,
                    number.text.strip(),
                    "number",
                    "inconsistent",
                    f"{number.text.strip()} has a minus sign. A concentration cannot be below zero, so this number "
                    "cannot be a lab value, detection limit or guideline limit.",
                )
            elif role != "result":
                self._check_other_number(s, number, role, analytes if isinstance(analytes, tuple) else ())
            elif isinstance(analytes, str):
                self._skip(s, number.text.strip(), analytes)
            else:
                self._check_result(s, number, analytes[0])

    def _check_result(self, s: _Sentence, number: NumberMention, analyte: str) -> None:
        """A number stated as the measured result of one analyte (or the sum): only that result can match."""
        value = number.value_ug
        assert value is not None
        quote, shown, label = number.text.strip(), self._shown(number), _label(analyte)
        result = "the sum of PFOS and PFHxS" if analyte == SUM_KEY else f"the {analyte} result"
        scope = self._number_scope(s, number)
        if scope.problem:
            elsewhere = [
                src.reference
                for r in self.rounds
                if (src := _result_source(r, analyte, value, less_than=number.less_than)) is not None
            ]
            where = f" The same value in the data: {'; '.join(elsewhere)}." if elsewhere else ""
            self._add(
                s,
                quote,
                "number",
                "untraced",
                f"{scope.problem} So {shown} cannot be traced to {result} on that date.{where}",
            )
            return
        rounds = scope.rounds or list(self.rounds)
        dated = f" on {', '.join(long_date(r.date) for r in scope.rounds)}" if scope.rounds else ""
        note = f" {scope.note}" if scope.note else ""
        matches = [
            src for r in rounds if (src := _result_source(r, analyte, value, less_than=number.less_than)) is not None
        ]
        if matches:
            refs = "; ".join(m.reference for m in matches)
            self._add(s, quote, "number", "consistent", f"{shown} traces to: {refs}.{note}", sources=matches)
            return
        data = "; ".join(_describe_value(r, analyte) for r in rounds)
        actual = [lab_source(row) for r in rounds for row in _value_of(r, analyte)[1]]
        if number.less_than:
            self._add(
                s,
                quote,
                "number",
                "inconsistent",
                f"'{shown}' says {label} was not detected{dated}, at a detection limit of {fmt_ug(value)}. "
                f"Data: {data}. This does not match.{note}",
                sources=actual,
            )
            return
        others = [t for t in self.traceables if t.value == value]
        claimed_dates = {r.date for r in scope.rounds}
        same_date = [t for t in others if claimed_dates and (not t.dates or t.dates & claimed_dates)]
        others = same_date or others  # show what the number is on the date the sentence names, when it is there
        if others:
            explanation = (
                f"The sentence gives {shown} as {result}{dated}, but in the data that value is: "
                + "; ".join(t.source.reference for t in others)
                + f". Data: {data}. This does not match."
            )
            not_detected = any(src.kind == "detection limit" for src in actual)
            if not_detected and any(t.is_detection_limit for t in others):
                explanation += (
                    f" {label} was not detected, and a detection limit is not a measured result. Write "
                    f"'<{fmt_ug(value)}' or 'not detected'."
                )
            self._add(
                s, quote, "number", "inconsistent", explanation + note, sources=[t.source for t in others] + actual
            )
            return
        self._add(
            s,
            quote,
            "number",
            "untraced",
            f"{shown} is not a lab value, detection limit, computed sum or difference, or guideline limit for "
            f"{self.well}." + (f" Data: {data}." if scope.rounds else "") + f" {self._hint(number, {analyte})}{note}",
        )

    def _check_other_number(
        self, s: _Sentence, number: NumberMention, role: NumberRole, analytes: tuple[str, ...]
    ) -> None:
        """A guideline value, detection limit, change between rounds, or a value compared against."""
        assert number.value_ug is not None
        target = abs(number.value_ug)  # a change may be written with a sign ("changed by -0.003 ug/L")
        quote, shown = number.text.strip(), self._shown(number)
        context: set[str] = set(analytes) | (set(SUM_MEMBERS) if SUM_KEY in analytes else set())
        candidates = [
            t for t in self.traceables if t.value == target and (t.is_detection_limit or not number.less_than)
        ]
        if _REFERENCE_BEFORE_VALUE.search(self._head(s, number.start)):
            # "down from 0.041 ug/L": a value compared against, so only a date in its own clause applies.
            clause_start, clause_end = self._clause_bounds(s, number.start)
            mentions = [d for d in s.dates if d.start >= clause_start and d.end <= clause_end]
        else:
            mentions = self._dates_for(s, number.start)
        near_dates = {r.date for d in mentions for r in d.resolve(self.rounds)}
        kinds = _ROLE_SOURCES[role]
        named_rule = next(iter(s.rules)) if role == "guideline" and len(s.rules) == 1 else None

        def fits(t: _Traceable) -> bool:
            if t.source.kind not in kinds or (named_rule is not None and t.source.rule != named_rule):
                return False
            if context and not t.analytes & context:
                return False
            if not near_dates or not t.dates:
                return True
            if role == "difference" and len(near_dates) > 1:
                return t.dates <= near_dates
            return bool(t.dates & near_dates)

        fitting = [t for t in candidates if fits(t)]
        if fitting:
            self._add(
                s,
                quote,
                "number",
                "consistent",
                f"{shown} traces to: " + "; ".join(t.source.reference for t in fitting) + ".",
                sources=[t.source for t in fitting],
            )
        elif candidates:
            about = "".join(
                (
                    f" for {', '.join(_label(a) for a in analytes)}" if analytes else "",
                    f" on {', '.join(long_date(d) for d in sorted(near_dates))}" if near_dates else "",
                    f" under {named_rule}" if named_rule else "",
                )
            )
            self._add(
                s,
                quote,
                "number",
                "inconsistent",
                f"{shown} appears in the data only as: "
                + "; ".join(t.source.reference for t in candidates)
                + f". The sentence gives it as {_ROLE_WORDS[role]}{about}, so it does not match.",
                sources=[t.source for t in candidates],
            )
        else:
            self._add(
                s,
                quote,
                "number",
                "untraced",
                f"{shown} is not a lab value, detection limit, computed sum or difference, or guideline limit for "
                f"{self.well}. {self._hint(number, context)}",
            )

    def _hint(self, number: NumberMention, analytes: set[str]) -> str:
        """Point at a likely unit slip (mg/L for ug/L), or else the nearest lab value."""
        value = number.value_ug
        assert value is not None
        wanted: set[str] = analytes | (set(SUM_MEMBERS) if SUM_KEY in analytes else set())
        pool = [t for t in self.traceables if t.source.kind == "lab result" and (not wanted or t.analytes & wanted)]
        if not pool:
            return "No lab value was written by code for this number."
        for exponent in (-3, 3):
            slipped = next((t for t in pool if t.value == value.scaleb(exponent)), None)
            if slipped is not None:
                size = "larger" if exponent < 0 else "smaller"
                return (
                    f"Check the unit: {number.text.strip()} is {fmt_ug(value)}, 1000 times {size} than the lab value "
                    f"{slipped.source.reference}."
                )
        closest = min(pool, key=lambda t: abs(t.value - value))
        return f"Nearest lab value: {closest.source.reference}."

    # --- shared claim helpers -------------------------------------------------------------------------------------

    def _clause_before(self, s: _Sentence, start: int, limit: int = 40) -> str:
        head = s.text[max(0, start - limit) : start]
        breaks = list(_CLAUSE_BREAK.finditer(head))
        return head[breaks[-1].end() :] if breaks else head

    def _negated(self, s: _Sentence, start: int) -> bool:
        return _NEGATION.search(self._clause_before(s, start)) is not None

    def _quote(self, s: _Sentence, start: int, end: int) -> str:
        """The trigger words, widened to include a negation such as "did not" when there is one."""
        clause = self._clause_before(s, start)
        negation = _NEGATION.search(clause)
        if negation is None:
            return s.text[start:end]
        return s.text[start - len(clause) + negation.start() : end]

    def _window_after(self, s: _Sentence, end: int, max_words: int = 7) -> str:
        """The few words after a comparison word, up to the end of its clause: where its target must be."""
        _, clause_end = self._clause_bounds(s, end)
        tail = s.text[end : min(clause_end, end + 100)]
        stop = _WINDOW_END.search(tail)
        tail = tail[: stop.start()] if stop else tail
        words = list(re.finditer(r"\S+", tail))
        return tail[: words[max_words - 1].end()] if len(words) > max_words else tail

    def _subject(self, s: _Sentence, start: int) -> AnalyteMention | None:
        before = [a for a in s.analytes if a.end <= start]
        if before:
            return before[-1]
        after = [a for a in s.analytes if a.start >= start]
        return after[0] if after else None

    def _subject_or_skip(self, s: _Sentence, start: int, quote: str) -> tuple[str, ...] | None:
        subject = self._subject(s, start)
        if subject is None:
            self._skip(s, quote, "No analyte is named in this sentence, so the claim cannot be matched to data.")
            return None
        if subject.is_group_word:
            self._skip(
                s,
                quote,
                "'PFAS' names a group of chemicals. Name the compound (for example PFOS) so it can be checked.",
            )
            return None
        return subject.analytes

    def _resolve_one(self, mention: DateMention) -> Round | str:
        matches = mention.resolve(self.rounds)
        if len(matches) == 1:
            return matches[0]
        available = ", ".join(long_date(r.date) for r in self.rounds)
        if not matches:
            return f"There is no {self.well} sampling round in {mention.label()}. Rounds with data: {available}."
        return f"{mention.label()} matches more than one round ({available}). Give the day."

    def _rounds_for_state_claim(self, s: _Sentence, pos: int) -> _Resolved:
        """Pick the rounds a guideline or detection claim at ``pos`` refers to."""
        if _ALL_ROUNDS.search(s.text):
            return _Resolved(list(self.rounds), note="Read as every monitoring round with data.")
        resolved: list[Round] = []
        for mention in self._dates_for(s, pos):
            found = self._resolve_one(mention)
            if isinstance(found, str):
                return _Resolved([], problem=found)
            resolved.append(found)
        latest_overall = self.rounds[-1]
        now = _NOW.search(s.text)
        if now:
            word = now.group(0).capitalize()
            if resolved:
                chosen = max(resolved, key=lambda r: r.date)
                note = f"'{word}' was read as {long_date(chosen.date)}, the latest date in this sentence."
            else:
                chosen = latest_overall
                note = f"'{word}' was read as the latest round with data, {long_date(chosen.date)}."
            return _Resolved([chosen], note=note, read_as_now=True)
        if not resolved and s.implied:
            implied = list({r.date: r for r in s.implied}.values())
            dates = " and ".join(long_date(r.date) for r in implied)
            return _Resolved(implied, note=f"No date is written; {dates} was taken from the lab value quoted.")
        if not resolved:
            return _Resolved([], problem="No date is given, so the claim cannot be matched to a monitoring round.")
        return _Resolved(list({r.date: r for r in resolved}.values()))

    # --- (d) detection claims -----------------------------------------------------------------------------------

    def _check_detection_claims(self, s: _Sentence) -> None:
        for m in _DETECTION.finditer(s.text):
            if not s.claimed.free(m.start(), m.end()):
                continue
            s.claimed.add(m.start(), m.end())
            claims_detected = m.group("pos") is not None
            if self._negated(s, m.start()):
                claims_detected = not claims_detected
            self._evaluate_detection(s, self._quote(s, m.start(), m.end()), m.start(), claims_detected)

    def _evaluate_detection(self, s: _Sentence, quote: str, start: int, claims_detected: bool) -> None:
        analytes = self._subject_or_skip(s, start, quote)
        if analytes is None:
            return
        chosen = self._rounds_for_state_claim(s, start)
        if chosen.problem:
            self._skip(s, quote, chosen.problem)
            return
        for analyte in analytes:
            if analyte == SUM_KEY:
                self._skip(s, quote, "Detection is reported per compound, not for the sum.")
                continue
            rows = [r.results.get(analyte) for r in chosen.rounds]
            if any(r is None for r in rows):
                self._skip(s, quote, f"No {analyte} result for one of the dates.")
                continue
            results = [r for r in rows if r is not None]
            wrong = [r for r in results if r.detected != claims_detected]
            said = "detected" if claims_detected else "not detected"
            evidence = "; ".join(r.describe() for r in results)
            status: CheckStatus = "inconsistent" if wrong else "consistent"
            verdict = "This does not match the lab results." if wrong else "This matches the lab results."
            explanation = f"The sentence says {analyte} was {said}. Lab results: {evidence}. {verdict}"
            if chosen.note:
                explanation += f" {chosen.note}"
            self._add(s, quote, "detection", status, explanation, sources=[lab_source(r) for r in results])

    # --- (c) comparison claims ----------------------------------------------------------------------------------

    def _check_comparison_claims(self, s: _Sentence) -> None:
        for m in _COMPARISON.finditer(s.text):
            if not s.claimed.free(m.start(), m.end()):
                continue
            word = m.group(0)
            window = self._window_after(s, m.end())
            if word.lower() == "under" and _RULE_REFERENCE_AFTER_UNDER.match(window):
                continue  # "under the current rule" names a rule; it is not a comparison
            op: Op = "<=" if m.group("le") else ">" if m.group("gt") else "<"
            if self._negated(s, m.start()):
                op = NEGATED[op]
            number = next(
                (
                    n
                    for n in s.numbers
                    if n.value_ug is not None
                    and n.start >= m.end()
                    and re.fullmatch(
                        r"\s*(?:the\s+)?(?:(?:value|level|concentration)\s+of\s+)?", s.text[m.end() : n.start]
                    )
                ),
                None,
            )
            if _LOR_TARGET.search(window):
                s.claimed.add(m.start(), m.end())
                quote = self._quote(s, m.start(), m.end()) + window
                self._evaluate_detection(s, quote, m.start(), claims_detected=op in (">", ">="))
            elif _OTHER_SCENARIO.search(window):
                s.claimed.add(m.start(), m.end())
                self._skip(
                    s,
                    f"{word}{window}".strip(),
                    "Only drinking-water guideline values are loaded, so comparisons with other guidelines "
                    "(recreational, ecological, soil) are not checked.",
                )
            elif (target := _GUIDELINE_TARGET.search(window)) or (number is None and word.lower().startswith("exceed")):
                s.claimed.add(m.start(), m.end())
                if target:
                    tail = _TARGET_TAIL.match(window, target.end())
                    shown = window[: tail.end() if tail else target.end()]
                else:
                    shown = window.rstrip()
                self._evaluate_guideline(s, self._quote(s, m.start(), m.end()) + shown, m.start(), op)
            elif number is not None:
                s.claimed.add(m.start(), m.end())
                assert number.value_ug is not None
                quote = self._quote(s, m.start(), m.end()) + s.text[m.end() : number.end]
                self._evaluate_threshold(s, quote, m.start(), op, number.value_ug)

    def _state_values(
        self, analyte: str, rounds: Sequence[Round]
    ) -> tuple[list[tuple[Round, Decimal, Decimal]], list[LabResult]]:
        """(round, low, high) bounds for an analyte in each round; non-detects span 0 to the detection limit."""
        out: list[tuple[Round, Decimal, Decimal]] = []
        rows: list[LabResult] = []
        for round_ in rounds:
            members = SUM_MEMBERS if analyte == SUM_KEY else (analyte,)
            found = [round_.results.get(name) for name in members]
            present = [r for r in found if r is not None]
            if len(present) != len(found):
                continue
            rows.extend(present)
            low = sum((r.value for r in present if r.value is not None), Decimal(0))
            high = low + sum((r.detection_limit for r in present if r.value is None), Decimal(0))
            out.append((round_, low, high))
        return out, rows

    def _evaluate_threshold(self, s: _Sentence, quote: str, start: int, op: Op, threshold: Decimal) -> None:
        analytes = self._subject_or_skip(s, start, quote)
        if analytes is None:
            return
        chosen = self._rounds_for_state_claim(s, start)
        if chosen.problem:
            self._skip(s, quote, chosen.problem)
            return
        for analyte in analytes:
            values, rows = self._state_values(analyte, chosen.rounds)
            if len(values) != len(chosen.rounds):
                self._skip(s, quote, f"No {_label(analyte)} result for one of the dates.")
                continue
            outcomes = [compare_bounds(low, high, op, threshold) for _, low, high in values]
            holds = _all(outcomes)
            evidence = "; ".join(_describe_value(r, analyte) for r, _, _ in values)
            words = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below"}[op]
            status: CheckStatus = "consistent" if holds else "inconsistent" if holds is False else "needs_judgement"
            explanation = f"The sentence says {_label(analyte)} was {words} {fmt_ug(threshold)}. Data: {evidence}. " + (
                "This matches."
                if holds
                else "This does not match."
                if holds is False
                else "A non-detect makes this impossible to confirm; a person should decide."
            )
            if chosen.note:
                explanation += f" {chosen.note}"
            self._add(s, quote, "guideline", status, explanation, sources=[lab_source(r) for r in rows])

    def _evaluate_guideline(self, s: _Sentence, quote: str, start: int, op: Op) -> None:
        analytes = self._subject_or_skip(s, start, quote)
        if analytes is None:
            return
        if len(s.rules) > 1:
            self._skip(
                s,
                quote,
                "This sentence names both rules, so it is not clear which comparison belongs to which rule. "
                "Use compare_rules for this round, or split the sentence.",
            )
            return
        chosen = self._rounds_for_state_claim(s, start)
        if chosen.problem:
            self._skip(s, quote, chosen.problem)
            return
        named = next(iter(s.rules), None)
        words = {">": "above", ">=": "at or above", "<": "below", "<=": "not above"}[op]
        for analyte in analytes:
            outcomes: list[RuleOutcome] = []
            sources: list[SourceRef] = []
            summaries: list[str] = []
            for rule in self.rules:
                outcome, used, short = self._rule_outcome(rule, analyte, chosen.rounds, op)
                outcomes.append(outcome)
                sources.extend(used)
                summaries.append(short)
            status, verdict = _guideline_verdict(outcomes, named)
            explanation = (
                f"The sentence says {_label(analyte)} is {words} the drinking-water guideline"
                + (f" ({self.data.rules[named].name})" if named else "")
                + f" on {', '.join(long_date(r.date) for r in chosen.rounds)}. "
                + " ".join(summaries)
                + f" {verdict}"
            )
            if chosen.note:
                explanation += f" {chosen.note}"
            if chosen.read_as_now and (later := self._later_round_note(analyte, chosen.rounds, op)):
                explanation += f" {later}"
            self._add(s, quote, "guideline", status, explanation, rule_outcomes=outcomes, sources=_dedupe(sources))

    def _rule_outcome(
        self, rule: Rule, analyte: str, rounds: Sequence[Round], op: Op
    ) -> tuple[RuleOutcome, list[SourceRef], str]:
        """Evaluate a guideline claim under one rule: (outcome, sources, one-sentence summary)."""
        limits = limits_for_analyte(rule, analyte)
        if not limits:
            reason = (
                "it screens PFOS and PFHxS separately and has no value for their sum."
                if analyte == SUM_KEY
                else f"it has no drinking-water value for {analyte}."
            )
            text = f"Not applicable under {rule.id}: {reason}"
            return RuleOutcome(rule=rule.id, holds=None, explanation=text), [], text
        limit = limits[0]
        # A claim about PFOS or PFHxS under a sum value compares that analyte alone (footnote a), not the sum.
        alone = limit.applies_to == "sum" and analyte != SUM_KEY
        prefix = f"{member_of_sum_note(analyte)} " if alone else ""
        results: list[bool | None] = []
        parts: list[str] = []
        sources: list[SourceRef] = []
        for round_ in rounds:
            screen = screen_member(limit, analyte, round_) if alone else screen_limit(limit, round_)
            sources.extend(lab_source(r) for r in screen.results)
            if screen.low is None or screen.high is None:
                results.append(None)
            else:
                results.append(compare_bounds(screen.low, screen.high, op, limit.value))
            dated = f"{long_date(round_.date)}: " if len(rounds) > 1 else ""
            context = ""
            if alone:
                # The sum is shown too, so a sentence about the wrong quantity can be reworded, never judged on it.
                total = screen_limit(limit, round_)
                sources.extend(lab_source(r) for r in total.results if r not in screen.results)
                context = f" For comparison, the sum of PFOS and PFHxS: {total.explanation}"
            parts.append(f"{dated}{screen.explanation}{context}")
        sources.append(limit_source(rule, limit))
        holds = _all(results)
        verdict = "holds" if holds else "does not hold" if holds is False else "cannot be confirmed"
        detail = f"{prefix}{' '.join(parts)}"
        explanation = f"The claim {verdict}. {detail} Source: {rule.citation}."
        short = f"Under {rule.id} the claim {verdict}: {detail}"
        return RuleOutcome(rule=rule.id, holds=holds, explanation=explanation), sources, short

    def _later_round_note(self, analyte: str, rounds: Sequence[Round], op: Op) -> str | None:
        latest = self.rounds[-1]
        if not rounds or max(r.date for r in rounds) >= latest.date:
            return None
        verdicts: list[str] = []
        for rule in self.rules:
            outcome, _, _ = self._rule_outcome(rule, analyte, [latest], op)
            if outcome.holds is not None:
                verdicts.append(f"{'holds' if outcome.holds else 'does not hold'} under {rule.id}")
        joined = " and ".join(verdicts) if verdicts else "cannot be confirmed"
        return f"A later round exists ({long_date(latest.date)}); there the same claim {joined}."

    # --- (b) change claims --------------------------------------------------------------------------------------

    def _check_change_claims(self, s: _Sentence) -> None:
        for m in _CHANGE.finditer(s.text):
            if not s.claimed.free(m.start(), m.end()):
                continue
            word = m.group(0)
            if word.lower() in ("falls", "fall") and re.match(r"\s+(?:within|below|under)\b", s.text[m.end() :]):
                continue  # "falls within the guideline" is not a change claim
            s.claimed.add(m.start(), m.end())
            negated = self._negated(s, m.start())
            if m.group("changed"):
                # "has not changed" claims no change; "has changed" claims some change.
                direction: Direction = "same" if negated else "changed"
                negated = False
            else:
                direction = "up" if m.group("up") else "down" if m.group("down") else "same"
            self._evaluate_change(s, word, start=m.start(), end=m.end(), direction=direction, negated=negated)

    def _order_dates(self, s: _Sentence, start: int, comparative: bool) -> tuple[Round, Round, str | None] | str:
        """Return (subject, reference, note): the claim compares subject with reference."""
        since = [d for d in s.dates if self._is_since(s, d)]
        if since and len(s.dates) == 1:
            ref = self._resolve_one(since[0])
            if isinstance(ref, str):
                return ref
            latest = self.rounds[-1]
            if latest.date <= ref.date:
                return f"No round after {long_date(ref.date)} to compare with."
            return (
                latest,
                ref,
                f"'Since {since[0].label()}' was compared with the latest round, {long_date(latest.date)}.",
            )
        if len(s.dates) < 2 and s.implied:
            filled = self._fill_from_quoted_values(s, start, comparative)
            if filled is not None:
                return filled
        if len(s.dates) != 2:
            if not s.dates:
                return "No dates are given, so it is not clear which two rounds are compared."
            if len(s.dates) == 1:
                return "Only one date is given, so it is not clear which round it is compared with."
            return "More than two dates are given, so it is not clear which two rounds are compared."
        resolved: list[Round] = []
        for mention in s.dates:
            found = self._resolve_one(mention)
            if isinstance(found, str):
                return found
            resolved.append(found)
        first, second = resolved
        if first.date == second.date:
            return "Both dates point to the same round."
        if comparative:
            marker = _REF_MARKER.search(s.text, start)
            if marker is None:
                return "The sentence compares two dates but does not say which is compared with which (use 'than')."
            after = [i for i, d in enumerate(s.dates) if d.start >= marker.end()]
            if not after:
                return "Could not tell which date is the reference after 'than'."
            ref_index = after[0]
            return resolved[1 - ref_index], resolved[ref_index], None
        # Verbs of change (rose, fell) describe movement over time, so the two rounds are compared in time
        # order whatever order the sentence names them in.
        earlier, later = sorted(resolved, key=lambda r: r.date)
        return later, earlier, None

    def _fill_from_quoted_values(self, s: _Sentence, start: int, comparative: bool) -> tuple[Round, Round, str] | None:
        """Pair written dates with rounds pinned down by quoted lab values, when that gives exactly two rounds."""
        written: list[Round] = []
        for mention in s.dates:
            found = self._resolve_one(mention)
            if isinstance(found, str):
                return None
            written.append(found)
        rounds = {r.date: r for r in (*written, *s.implied)}
        if len(rounds) != 2:
            return None
        labels = " and ".join(long_date(d) for d in sorted(rounds))
        if comparative:
            marker = _REF_MARKER.search(s.text, start)
            refs = [r for d, r in zip(s.dates, written, strict=True) if marker and d.start >= marker.end()]
            if len(refs) != 1:
                return None
            reference = refs[0]
            subject = next(r for d, r in rounds.items() if d != reference.date)
            note = f"The rounds ({labels}) come from the date written after 'than' and the lab value quoted."
            return subject, reference, note
        earlier, later = sorted(rounds.values(), key=lambda r: r.date)
        note = f"The rounds ({labels}) were taken from the dates and lab values quoted, and compared in time order."
        return later, earlier, note

    def _evaluate_change(
        self,
        s: _Sentence,
        word: str,
        *,
        start: int,
        end: int,
        direction: Direction,
        negated: bool,
    ) -> None:
        quote = self._quote(s, start, end)
        comparative = _COMPARATIVE.match(word) is not None
        analytes = self._subject_or_skip(s, start, word)
        if analytes is None:
            return
        if comparative and (marker := _REF_MARKER.search(s.text, start, start + 60)) is not None:
            other = next((a for a in s.analytes if 0 <= a.start - marker.end() <= 12), None)
            if other is not None:
                self._skip(
                    s,
                    quote,
                    f"This compares {_label(analytes[0])} with {other.text}, two analytes rather than two dates. "
                    "Comparisons between analytes are not checked yet.",
                )
                return
        ordered = self._order_dates(s, start, comparative=comparative)
        if isinstance(ordered, str):
            self._skip(s, quote, ordered)
            return
        subject, reference, note = ordered
        qualifier = _QUALIFIER.search(s.text[max(0, start - 25) : end + 25])
        for analyte in analytes:
            after, after_rows = _value_of(subject, analyte)
            before, before_rows = _value_of(reference, analyte)
            if after is None or before is None:
                self._skip(
                    s,
                    quote,
                    f"{_label(analyte)} was not detected (or not analysed) in one of the two rounds, so a change "
                    "cannot be measured.",
                )
                continue
            diff = after - before
            pct = percent_change(before, after)
            size = f"{fmt_ug(abs(diff))}" + (f" ({abs(pct)}%)" if pct is not None else "")
            if comparative:
                relation = "the same" if diff == 0 else f"{size} {'higher' if diff > 0 else 'lower'}"
                facts = (
                    f"{_label(analyte)} was {fmt_ug(after)} on {long_date(subject.date)} and {fmt_ug(before)} on "
                    f"{long_date(reference.date)}, so it was {relation} on {long_date(subject.date)}."
                )
            else:
                movement = "no change" if diff == 0 else f"{'a rise' if diff > 0 else 'a fall'} of {size}"
                facts = (
                    f"{_label(analyte)} went from {fmt_ug(before)} on {long_date(reference.date)} to {fmt_ug(after)} "
                    f"on {long_date(subject.date)}: {movement}."
                )
            status, claim = _change_verdict(direction, negated, diff, comparative=comparative)
            explanation = f"{facts} {claim}"
            if qualifier:
                q = qualifier.group(1).lower()
                if q == "significantly":
                    explanation += " 'Significantly' can suggest a statistical test; none was run here."
                else:
                    explanation += f" '{q.capitalize()}' is a judgement word; a person should decide whether it fits."
            if note:
                explanation += f" {note}"
            sources = [lab_source(r) for r in (*before_rows, *after_rows)]
            self._add(s, quote, "change", status, explanation, sources=sources)

    # --- report -------------------------------------------------------------------------------------------------

    def _report(self, sentences: list[str]) -> ParagraphCheck:
        self.checked.sort(key=lambda item: (item.sentence, sentences[item.sentence - 1].find(item.quote)))
        counts: dict[str, int] = {}
        for item in self.checked:
            counts[item.status] = counts.get(item.status, 0) + 1
        order = ("consistent", "inconsistent", "untraced", "depends_on_rule", "needs_judgement")
        words = {
            "consistent": ("matches the data", "match the data"),
            "inconsistent": ("does not match the data", "do not match the data"),
            "untraced": ("could not be traced", "could not be traced"),
            "depends_on_rule": ("depends on which rule is used", "depend on which rule is used"),
            "needs_judgement": ("needs a person to judge", "need a person to judge"),
        }
        breakdown = ", ".join(f"{counts[k]} {words[k][0 if counts[k] == 1 else 1]}" for k in order if k in counts)
        attention = sum(v for k, v in counts.items() if k != "consistent")
        skipped = len(self.not_checked)
        summary = (
            f"Checked {_plural(len(self.checked), 'item')} in {_plural(len(sentences), 'sentence')}"
            + (f": {breakdown}." if breakdown else ".")
            + f" Needing a person to decide: {attention}."
            + f" Not checked: {skipped} (listed under not_checked with reasons)."
            + " Statements outside the checker's scope were not checked either."
        )
        notes = [
            "A result equal to the limit is not above it.",
            "A guideline limit is an investigation level, not a finding that the water is unsafe or the site is "
            "contaminated.",
            RULE_CHOICE,
        ]
        return ParagraphCheck(
            well=self.well,
            summary=summary,
            scope=SCOPE,
            sentences=sentences,
            checked=self.checked,
            not_checked=self.not_checked,
            needs_attention=attention,
            notes=notes,
        )


# --- verdict helpers -----------------------------------------------------------------------------------------------


def _near(positions: set[int], pos: int, *, before: bool = False) -> bool:
    """True when one of ``positions`` is at most two characters before ``pos`` (or after it)."""
    window = range(pos - 2, pos + 1) if before else range(pos, pos + 3)
    return any(p in positions for p in window)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _all(outcomes: Sequence[bool | None]) -> bool | None:
    if any(o is False for o in outcomes):
        return False
    if any(o is None for o in outcomes):
        return None
    return True


def _change_verdict(
    direction: Direction, negated: bool, diff: Decimal, *, comparative: bool = False
) -> tuple[CheckStatus, str]:
    if direction == "changed":
        if diff != 0:
            return "consistent", "The sentence says it changed, which matches."
        return "inconsistent", "The sentence says it changed, but the values are identical."
    if comparative:
        said = {"up": "was higher", "down": "was lower", "same": "was similar"}[direction]
        if negated:
            said = {"up": "was not higher", "down": "was not lower", "same": "was not similar"}[direction]
    else:
        said = {"up": "went up", "down": "went down", "same": "stayed similar"}[direction]
        if negated:
            said = {"up": "did not go up", "down": "did not go down", "same": "did not stay similar"}[direction]
    if direction == "same":
        if diff == 0:
            status: CheckStatus = "inconsistent" if negated else "consistent"
            return status, f"The sentence says it {said}; the values are identical."
        return (
            "needs_judgement",
            f"The sentence says it {said}. Whether this change counts as similar is the scientist's call; "
            "Evidenceline does not set a threshold.",
        )
    holds = diff > 0 if direction == "up" else diff < 0
    if negated:
        holds = not holds
    if holds:
        return "consistent", f"The sentence says it {said}, which matches."
    return "inconsistent", f"The sentence says it {said}, which does not match."


def _guideline_verdict(outcomes: Sequence[RuleOutcome], named: str | None) -> tuple[CheckStatus, str]:
    if named is not None:
        chosen = next(o for o in outcomes if o.rule == named)
        if chosen.holds is True:
            return "consistent", f"The sentence names {named}, and under it the claim holds."
        if chosen.holds is False:
            return "inconsistent", f"The sentence names {named}, and under it the claim does not hold."
        return "needs_judgement", f"The sentence names {named}, but the claim cannot be confirmed under it."
    holding = [o.rule for o in outcomes if o.holds is True]
    failing = [o.rule for o in outcomes if o.holds is False]
    if len(holding) == len(outcomes):
        return "consistent", "The claim holds under every rule."
    if holding:
        return (
            "depends_on_rule",
            f"The sentence does not say which rule it uses. The claim holds under {', '.join(holding)} only. "
            f"{RULE_CHOICE}",
        )
    if failing:
        if len(failing) == len(outcomes):
            return "inconsistent", "The claim does not hold under either rule."
        return (
            "inconsistent",
            f"The claim does not hold under {', '.join(failing)}, and the other rule has no value to test it against.",
        )
    return "needs_judgement", "The claim cannot be confirmed under either rule; a person should decide."


def _dedupe(sources: Sequence[SourceRef]) -> list[SourceRef]:
    seen: dict[str, SourceRef] = {}
    for source in sources:
        seen.setdefault(source.reference, source)
    return list(seen.values())


def check_paragraph_text(data: Dataset, text: str, well: str) -> ParagraphCheck:
    """Run the checker over ``text`` for ``well``."""
    return _ParagraphChecker(data, well).run(text)
