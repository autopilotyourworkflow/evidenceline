"""What a check hands back: draft review items (numbered later), passes and things it could not check."""

from __future__ import annotations

from dataclasses import dataclass

from evidenceline.dataset import long_date
from evidenceline.tidy.models import CheckName
from evidenceline.tidy.records import CocRow, FieldRow, LabRow


@dataclass(frozen=True, slots=True)
class Evidence:
    """One row in one file that supports a finding."""

    file: str
    row: int
    sample_id: str | None
    analyte: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class Draft:
    """A review item before numbering."""

    check: CheckName
    title: str
    found: str
    evidence: tuple[Evidence, ...]
    rule: str
    source: str
    scientist_decides: str
    samples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Passed:
    """Something checked and not flagged, said plainly."""

    check: CheckName
    what: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class Skipped:
    """Something the check could not look at, and why."""

    check: CheckName
    what: str
    reason: str


@dataclass(frozen=True, slots=True)
class Outcome:
    """Everything one check produced."""

    check: CheckName
    what_was_checked: str
    rule: str
    source: str
    checked: int
    drafts: tuple[Draft, ...] = ()
    passed: tuple[Passed, ...] = ()
    skipped: tuple[Skipped, ...] = ()


def lab_evidence(row: LabRow, detail: str | None = None) -> Evidence:
    text = detail or f"{row.sample_id}, {row.analyte} {row.shown_both()}"
    return Evidence(file=row.file, row=row.row, sample_id=row.sample_id, analyte=row.analyte, detail=text)


def coc_evidence(row: CocRow, detail: str | None = None) -> Evidence:
    hold = ", HOLD" if row.hold else ""
    text = detail or f"{row.sample_id} on {row.coc_number}, sampled {row.sampled.written}{hold}"
    return Evidence(file=row.file, row=row.row, sample_id=row.sample_id, analyte=None, detail=text)


def field_evidence(row: FieldRow, detail: str | None = None) -> Evidence:
    equipment = f", {row.equipment}" if row.equipment else ""
    text = detail or f"{row.sample_id}, {row.qa_type.replace('_', ' ')}, {long_date(row.sampled.date)}{equipment}"
    return Evidence(file=row.file, row=row.row, sample_id=row.sample_id, analyte=None, detail=text)


def join_words(items: list[str]) -> str:
    """``["a", "b", "c"]`` becomes ``"a, b and c"``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else (many or word + 's')}"
