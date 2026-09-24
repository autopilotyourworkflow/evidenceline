"""Deterministic extraction of dates, numbers, analytes and rule names from report prose.

No language model is involved. Each finder returns spans so later steps can tell which words belong to which
claim and so the same characters are never read twice (a date's year is not also read as a bare number).
"""

from __future__ import annotations

import bisect
import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from evidenceline.dataset import SUM_KEY, Round
from evidenceline.units import UNIT_PATTERN, normalise_unit, parse_decimal, to_ug_per_l

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip
_MONTH = "(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<day>[0-3]?\d)(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>(?:19|20)\d\d)"

_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?P<year>(?:19|20)\d\d)-(?P<mnum>[01]\d)-(?P<day>[0-3]\d)\b"),
    re.compile(rf"\b{_DAY}\s+{_MONTH},?\s+{_YEAR}\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTH}\s+{_DAY},\s*{_YEAR}\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTH},?\s+{_YEAR}\b", re.IGNORECASE),
)
_AMBIGUOUS_NUMERIC_DATE = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
_YEAR_ONLY = re.compile(r"(?<![\w.])(?:19|20)\d\d(?!\w|\.\d)")

_NUMBER = re.compile(
    # A minus sign counts only when it stands on its own ("was -0.038"), not inside "MB-2" or "L-1". A whole
    # number right after a hyphen that follows a letter ("MB-2", "ug L-1") is part of a name or unit, not a value.
    r"(?:(?<![\w.\-\u2212])(?P<neg>[-\u2212])(?=\.?\d))?"
    r"(?<![\w.])(?P<lt><\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\.\d+|(?<![A-Za-z][-\u2212])\d+)(?![\d.]\d)"
    rf"(?:\s*(?P<unit>{UNIT_PATTERN})(?![a-z]))?",
    re.IGNORECASE,
)

_RULE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nemp-3.0", re.compile(r"(?:PFAS\s+)?NEMP\s*(?:v(?:ersion)?\.?\s*)?3\.0\b|\bNEMP\s*3\b(?!\.\d)", re.IGNORECASE)),
    (
        "current",
        re.compile(
            r"(?:PFAS\s+)?NEMP\s*(?:v(?:ersion)?\.?\s*)?3\.1\b|\bADWG\b(?:\s*\(?\s*2025\s*\)?)?"
            r"|\bNHMRC\b(?:\s*\(?\s*2025\s*\)?)?|\bcurrent\s+(?:drinking[- ]water\s+)?(?:guideline|limit|value)s?\b",
            re.IGNORECASE,
        ),
    ),
)

_SYNONYMS: dict[str, str] = {
    "perfluorooctane sulfonate": "PFOS",
    "perfluorooctane sulphonate": "PFOS",
    "perfluorooctane sulfonic acid": "PFOS",
    "perfluorooctanesulfonic acid": "PFOS",
    "perfluorohexane sulfonate": "PFHxS",
    "perfluorohexane sulphonate": "PFHxS",
    "perfluorohexane sulfonic acid": "PFHxS",
    "perfluorohexanesulfonic acid": "PFHxS",
    "perfluorooctanoic acid": "PFOA",
    "perfluorooctanoate": "PFOA",
    "perfluorobutane sulfonate": "PFBS",
    "perfluorobutane sulfonic acid": "PFBS",
}

_WELL_ID = re.compile(r"\b(?:MB|MW|BH|GW|PZ)-?\d{1,3}[A-Z]?\b|(?<=\bwell\s)[A-Z]{1,4}-?\d{1,3}[A-Z]?\b")


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class DateMention(Span):
    year: int
    month: int
    day: int | None

    def label(self) -> str:
        month = dt.date(self.year, self.month, 1).strftime("%B")
        return f"{self.day} {month} {self.year}" if self.day else f"{month} {self.year}"

    def matches(self, value: dt.date) -> bool:
        return value.year == self.year and value.month == self.month and (self.day is None or value.day == self.day)

    def resolve(self, rounds: Sequence[Round]) -> list[Round]:
        return [r for r in rounds if self.matches(r.date)]


@dataclass(frozen=True, slots=True)
class NumberMention(Span):
    number_text: str
    unit_text: str | None
    less_than: bool
    value_ug: Decimal | None
    """Exact value in ug/L (negative when written with a minus sign), or None without a known concentration unit."""
    negative: bool = False


@dataclass(frozen=True, slots=True)
class AnalyteMention(Span):
    analytes: tuple[str, ...]
    """One analyte, several (a coordinated subject such as "PFOS and PFHxS"), or ``(SUM_KEY,)``."""
    is_group_word: bool = False
    """True for a vague group word such as "PFAS" that does not name one compound."""


@dataclass(frozen=True, slots=True)
class RuleMention(Span):
    rule: str


class SpanSet:
    """Tracks consumed character ranges so overlapping finders do not double count.

    Ranges are kept sorted and merged, so each lookup is a binary search (long sentences stay fast).
    """

    def __init__(self) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []

    def free(self, start: int, end: int) -> bool:
        index = bisect.bisect_left(self._starts, end) - 1
        return index < 0 or self._ends[index] <= start

    def add(self, start: int, end: int) -> None:
        first = bisect.bisect_left(self._ends, start)
        last = bisect.bisect_right(self._starts, end)
        if first < last:  # merge with every range this one touches
            start = min(start, self._starts[first])
            end = max(end, self._ends[last - 1])
        self._starts[first:last] = [start]
        self._ends[first:last] = [end]


def find_rules(text: str, used: SpanSet) -> list[RuleMention]:
    found: list[RuleMention] = []
    for rule, pattern in _RULE_PATTERNS:
        for m in pattern.finditer(text):
            if used.free(m.start(), m.end()):
                used.add(m.start(), m.end())
                found.append(RuleMention(m.start(), m.end(), m.group(0), rule))
    return sorted(found, key=lambda r: r.start)


def find_dates(text: str, used: SpanSet) -> tuple[list[DateMention], list[Span]]:
    """Return recognised dates and spans of date-like text that could not be read confidently."""
    dates: list[DateMention] = []
    unclear: list[Span] = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            if not used.free(m.start(), m.end()):
                continue
            groups = m.groupdict()
            month = int(groups["mnum"]) if groups.get("mnum") else _MONTHS[groups["month"].lower()]
            day = int(groups["day"]) if groups.get("day") else None
            year = int(groups["year"])
            used.add(m.start(), m.end())
            try:
                dt.date(year, month, day or 1)
            except ValueError:
                unclear.append(Span(m.start(), m.end(), m.group(0)))  # for example 31 February
                continue
            dates.append(DateMention(m.start(), m.end(), m.group(0), year, month, day))
    for m in _AMBIGUOUS_NUMERIC_DATE.finditer(text):
        if used.free(m.start(), m.end()):
            used.add(m.start(), m.end())
            unclear.append(Span(m.start(), m.end(), m.group(0)))
    return sorted(dates, key=lambda d: d.start), sorted(unclear, key=lambda u: u.start)


def find_years(text: str, used: SpanSet) -> list[Span]:
    """Bare years such as "in 2025", found after full dates are consumed."""
    years: list[Span] = []
    for m in _YEAR_ONLY.finditer(text):
        if used.free(m.start(), m.end()):
            used.add(m.start(), m.end())
            years.append(Span(m.start(), m.end(), m.group(0)))
    return years


def find_numbers(text: str, used: SpanSet) -> list[NumberMention]:
    numbers: list[NumberMention] = []
    for m in _NUMBER.finditer(text):
        if not used.free(m.start(), m.end()):
            continue
        used.add(m.start(), m.end())
        unit_text = m.group("unit")
        negative = m.group("neg") is not None
        value: Decimal | None = None
        if unit_text is not None and normalise_unit(unit_text) is not None:
            value = to_ug_per_l(parse_decimal(m.group("num")), unit_text)
            if negative:
                value = -value
        numbers.append(
            NumberMention(
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                number_text=m.group("num"),
                unit_text=unit_text,
                less_than=m.group("lt") is not None,
                value_ug=value,
                negative=negative,
            )
        )
    return numbers


def _name_pattern(names: Iterable[str]) -> str:
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


def find_analytes(text: str, known: Sequence[str], used: SpanSet) -> list[AnalyteMention]:
    """Find analyte mentions. Order matters: sums, then coordinated groups, then single names."""
    canonical = {name.lower(): name for name in known} | _SYNONYMS
    names = _name_pattern(canonical)
    single = rf"(?:{names})"
    patterns: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(
                r"(?:the\s+)?(?:sum|total|combined\s+(?:concentrations?|total))\s+(?:concentrations?\s+)?of\s+"
                r"(?:PFOS\s+(?:and|\+|plus)\s+PFHxS|PFHxS\s+(?:and|\+|plus)\s+PFOS)"
                r"|\bPFOS\s*(?:\+|plus)\s*PFHxS\b|\bPFHxS\s*(?:\+|plus)\s*PFOS\b"
                r"|\bcombined\s+PFOS\s+(?:and|\+)\s+PFHxS\b"
                r"|\bPFOS\s+(?:and|\+)\s+PFHxS\s+combined\b",
                re.IGNORECASE,
            ),
            "sum",
        ),
        # A list needs a final "and" ("PFOS, PFHxS and PFOA"). A bare comma does not join two names: in
        # "Unlike PFOS, PFHxS rose" the comma ends a phrase.
        (
            re.compile(rf"(?<![\w:]){single}(?:\s*,\s*{single})*\s*,?\s*and\s+{single}(?!\w)", re.IGNORECASE),
            "group",
        ),
        (re.compile(rf"(?<![\w:]){single}(?!\w)", re.IGNORECASE), "single"),
        (re.compile(r"\bPFAS\b"), "vague"),
    ]
    single_re = re.compile(rf"(?<![\w:]){single}(?!\w)", re.IGNORECASE)
    found: list[AnalyteMention] = []
    for pattern, kind in patterns:
        for m in pattern.finditer(text):
            if not used.free(m.start(), m.end()):
                continue
            used.add(m.start(), m.end())
            if kind == "sum":
                analytes: tuple[str, ...] = (SUM_KEY,)
            elif kind == "vague":
                analytes = ()
            else:
                members = [canonical[s.group(0).lower()] for s in single_re.finditer(m.group(0))]
                analytes = tuple(dict.fromkeys(members))
            found.append(AnalyteMention(m.start(), m.end(), m.group(0), analytes, is_group_word=kind == "vague"))
    return sorted(found, key=lambda a: a.start)


def find_well_ids(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).upper().replace("-", "") for m in _WELL_ID.finditer(text)))


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])|\n\s*\n")


def split_sentences(text: str) -> list[str]:
    parts = (p.strip() for p in _SENTENCE_BREAK.split(text.strip()))
    return [re.sub(r"\s+", " ", p) for p in parts if p]
