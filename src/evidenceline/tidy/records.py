"""Turn the three raw tables (lab results, chain of custody, field sheet) into typed, exact records.

Column names are Evidenceline's own documented schema (see ``data/fds01_site/README.md`` for the mapping to
ESdat-style names). Every record keeps its file name and row number.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, get_args

from evidenceline.errors import EvidencelineError
from evidenceline.tidy.dates import Stamp, parse_clock, parse_date, parse_stamp
from evidenceline.tidy.files import Record, Table
from evidenceline.tidy.units import Matrix, Unit, parse_matrix, parse_unit
from evidenceline.units import fmt, parse_decimal

LAB_FILE = "lab_results.csv"
COC_FILE = "chain_of_custody.csv"
FIELD_FILE = "field_sheet.csv"

LAB_COLUMNS = (
    "lab_report",
    "lab_sample_id",
    "sample_id",
    "sampled",
    "matrix",
    "analyte",
    "prefix",
    "result",
    "unit",
    "lor",
    "lor_unit",
    "extracted",
)
COC_COLUMNS = ("coc_number", "sample_id", "sampled", "matrix", "analyses", "hold", "comments")
FIELD_COLUMNS = ("sample_id", "date", "time", "location", "matrix", "equipment", "qa_type", "parent_sample", "notes")

QaType = Literal["primary", "field_duplicate", "rinsate_blank", "field_blank", "trip_blank", "container_blank"]
QA_TYPES: tuple[str, ...] = get_args(QaType)
BLANK_TYPES: frozenset[str] = frozenset({"rinsate_blank", "field_blank", "trip_blank", "container_blank"})


@dataclass(frozen=True, slots=True)
class LabRow:
    """One lab result. ``value`` and ``lor`` are exact, in the canonical unit for the matrix."""

    file: str
    row: int
    lab_report: str
    lab_sample_id: str
    sample_id: str
    sampled: Stamp
    matrix: Matrix
    analyte: str
    detected: bool
    reported: str
    unit: Unit
    value: Decimal | None
    lor: Decimal
    lor_unit: Unit
    reported_lor: str
    extracted: dt.date | None
    comment: str

    @property
    def canonical_unit(self) -> str:
        return self.unit.canonical

    def shown(self) -> str:
        """The result as reported, for example ``38 ng/L`` or ``<0.01 μg/L``."""
        return f"{self.reported} {self.unit.written}"

    def shown_canonical(self) -> str:
        """The result in the canonical unit, for example ``0.038 ug/L`` or ``<0.01 ug/L``."""
        number = fmt(self.value) if self.value is not None else f"<{fmt(self.lor)}"
        return f"{number} {self.canonical_unit}"

    def shown_both(self) -> str:
        """``38 ng/L (0.038 ug/L)`` when converted or respelled, else just ``0.41 mg/kg``."""
        if self.unit.written == self.canonical_unit:
            return self.shown()
        return f"{self.shown()} ({self.shown_canonical()})"

    def bound(self) -> tuple[Decimal, Decimal]:
        """Lowest and highest possible value: a non-detect lies between zero and its LOR."""
        return (self.value, self.value) if self.value is not None else (Decimal(0), self.lor)


@dataclass(frozen=True, slots=True)
class CocRow:
    file: str
    row: int
    coc_number: str
    sample_id: str
    sampled: Stamp
    matrix: Matrix
    analyses: str
    hold: bool
    comments: str


@dataclass(frozen=True, slots=True)
class FieldRow:
    file: str
    row: int
    sample_id: str
    sampled: Stamp
    location: str
    matrix: Matrix
    equipment: str
    qa_type: QaType
    parent_sample: str
    notes: str

    @property
    def is_blank(self) -> bool:
        return self.qa_type in BLANK_TYPES


def _where(table: Table, record: Record) -> str:
    return f"{table.name} row {record.row}"


def _number(text: str, what: str) -> Decimal:
    try:
        return parse_decimal(text)
    except ValueError:
        raise EvidencelineError(f"{what} {text!r} is not a plain number (for example 0.038).") from None


def _lab_row(table: Table, record: Record) -> LabRow:
    where = _where(table, record)
    try:
        matrix = parse_matrix(record.get("matrix"))
        unit = parse_unit(record.get("unit"))
        lor_unit = parse_unit(record.get("lor_unit") or record.get("unit"))
        for used in (unit, lor_unit):
            if used.matrix != matrix:
                raise EvidencelineError(
                    f"a {matrix} result cannot be in {used.written}; use a per-"
                    f"{'kilogram' if matrix == 'soil' else 'litre'} unit"
                )
        prefix = record.get("prefix")
        if prefix not in ("", "<"):
            raise EvidencelineError(f"prefix {prefix!r} is not understood; use '<' for not detected or leave it empty")
        result_text = record.get("result")
        number = _number(result_text, "The result")
        lor = lor_unit.to_canonical(_number(record.get("lor"), "The LOR"))
        if lor <= 0:
            raise EvidencelineError(
                f"the LOR must be above zero, but it is {record.get('lor')} {lor_unit.written}; a detection limit of "
                "zero or less cannot be right, so ask the lab to confirm it"
            )
        value = None if prefix == "<" else unit.to_canonical(number)
        if value == 0:
            raise EvidencelineError(
                "a detected result of zero is not understood; a result that was not detected is written with '<' "
                "and its LOR"
            )
        if value is None and unit.to_canonical(number) != lor:
            raise EvidencelineError(
                f"a '<' result must equal the LOR, but the result is {result_text} {unit.written} "
                f"and the LOR is {record.get('lor')} {lor_unit.written}"
            )
        sample_id = record.get("sample_id")
        analyte = record.get("analyte")
        if not sample_id or not analyte:
            raise EvidencelineError("sample_id and analyte must not be empty")
        extracted_text = record.get("extracted")
        return LabRow(
            file=table.name,
            row=record.row,
            lab_report=record.get("lab_report"),
            lab_sample_id=record.get("lab_sample_id"),
            sample_id=sample_id,
            sampled=parse_stamp(record.get("sampled")),
            matrix=matrix,
            analyte=analyte,
            detected=value is not None,
            reported=f"{prefix}{result_text}",
            unit=unit,
            value=value,
            lor=lor,
            lor_unit=lor_unit,
            reported_lor=record.get("lor"),
            extracted=parse_date(extracted_text) if extracted_text else None,
            comment=record.get("lab_comment"),
        )
    except EvidencelineError as exc:
        raise EvidencelineError(f"{where} could not be read: {exc}") from exc


def lab_rows(table: Table) -> tuple[LabRow, ...]:
    rows = tuple(_lab_row(table, record) for record in table.records)
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.sample_id, row.analyte)
        if key in seen:
            raise EvidencelineError(
                f"{table.name} has two results for {row.analyte} in {row.sample_id} (rows {seen[key]} and {row.row}). "
                "Remove one, or give the re-analysis its own sample id."
            )
        seen[key] = row.row
    return rows


def _unique_ids(table: Table, ids: list[tuple[str, int]]) -> None:
    seen: dict[str, int] = {}
    for sample_id, row in ids:
        if not sample_id:
            raise EvidencelineError(f"{table.name} row {row} has no sample_id.")
        if sample_id in seen:
            raise EvidencelineError(f"{table.name} lists {sample_id} twice (rows {seen[sample_id]} and {row}).")
        seen[sample_id] = row


def coc_rows(table: Table) -> tuple[CocRow, ...]:
    rows: list[CocRow] = []
    for record in table.records:
        where = _where(table, record)
        hold = record.get("hold").upper()
        if hold not in ("Y", "N", ""):
            raise EvidencelineError(f"{where} could not be read: hold must be Y or N, not {record.get('hold')!r}.")
        try:
            rows.append(
                CocRow(
                    file=table.name,
                    row=record.row,
                    coc_number=record.get("coc_number"),
                    sample_id=record.get("sample_id"),
                    sampled=parse_stamp(record.get("sampled")),
                    matrix=parse_matrix(record.get("matrix")),
                    analyses=record.get("analyses"),
                    hold=hold == "Y",
                    comments=record.get("comments"),
                )
            )
        except EvidencelineError as exc:
            raise EvidencelineError(f"{where} could not be read: {exc}") from exc
    _unique_ids(table, [(row.sample_id, row.row) for row in rows])
    return tuple(rows)


def _qa_type(raw: str) -> QaType:
    for known in get_args(QaType):
        if raw == known:
            return known
    raise EvidencelineError(f"qa_type {raw!r} is not one of: {', '.join(QA_TYPES)}")


def field_rows(table: Table) -> tuple[FieldRow, ...]:
    rows: list[FieldRow] = []
    for record in table.records:
        try:
            date = parse_stamp(record.get("date"))
            clock = parse_clock(record.get("time"))
            written = f"{record.get('date')} {record.get('time')}".strip()
            rows.append(
                FieldRow(
                    file=table.name,
                    row=record.row,
                    sample_id=record.get("sample_id"),
                    sampled=Stamp(date=date.date, time=clock or date.time, written=written),
                    location=record.get("location"),
                    matrix=parse_matrix(record.get("matrix")),
                    equipment=record.get("equipment"),
                    qa_type=_qa_type(record.get("qa_type").lower()),
                    parent_sample=record.get("parent_sample"),
                    notes=record.get("notes"),
                )
            )
        except EvidencelineError as exc:
            raise EvidencelineError(f"{_where(table, record)} could not be read: {exc}") from exc
    _unique_ids(table, [(row.sample_id, row.row) for row in rows])
    known = {row.sample_id for row in rows}
    for row in rows:
        if row.qa_type == "field_duplicate" and row.parent_sample not in known:
            raise EvidencelineError(
                f"{table.name} row {row.row}: duplicate {row.sample_id} names parent sample "
                f"{row.parent_sample or '(blank)'}, which is not on the field sheet."
            )
    return tuple(rows)
