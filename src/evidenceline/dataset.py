"""Load the lab result files and the guideline values into immutable, exact records.

The default data lives in ``evidenceline/data``: one EDD-style CSV per monitoring round (``*_lab.csv``) and
``guidelines.json``. Tests can point :func:`load_dataset` at another directory.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal, cast

from evidenceline.errors import EvidencelineError
from evidenceline.units import CANONICAL_UNIT, fmt, parse_decimal, to_ug_per_l

SUM_KEY = "PFOS+PFHxS"
"""Key used for the sum of PFOS and PFHxS."""
SUM_LABEL = "sum of PFOS and PFHxS"
"""How a value set for the sum of PFOS and PFHxS is named in tool output."""

SUM_MEMBERS: tuple[str, str] = ("PFOS", "PFHxS")

_REQUIRED_COLUMNS = (
    "lab_report_id",
    "site_id",
    "well_id",
    "sample_id",
    "sample_date",
    "matrix",
    "analyte",
    "result",
    "unit",
    "lor",
)


@dataclass(frozen=True, slots=True)
class LabResult:
    """One row of a lab file. ``value`` is None when the analyte was not detected."""

    well: str
    site: str
    matrix: str
    sample_id: str
    date: dt.date
    analyte: str
    reported: str
    value: Decimal | None
    detection_limit: Decimal
    lab_report: str
    file: str
    row: int

    @property
    def detected(self) -> bool:
        return self.value is not None

    def describe(self) -> str:
        """Short provenance, for example ``PFOS 0.038 ug/L, 16 September 2025 (SYN-250916, file.csv row 14)``."""
        shown = fmt(self.value) if self.value is not None else f"<{fmt(self.detection_limit)}"
        return (
            f"{self.analyte} {shown} {CANONICAL_UNIT}, {long_date(self.date)} "
            f"({self.lab_report}, {self.file} row {self.row})"
        )


@dataclass(frozen=True, slots=True)
class Round:
    """One monitoring round (one sample date) at one well."""

    well: str
    date: dt.date
    lab_report: str
    file: str
    results: Mapping[str, LabResult]


@dataclass(frozen=True, slots=True)
class Limit:
    """One guideline value within a rule."""

    rule_id: str
    key: str
    applies_to: Literal["single", "sum"]
    members: tuple[str, ...]
    value: Decimal
    scenario: str
    note: str

    @property
    def label(self) -> str:
        return SUM_LABEL if self.applies_to == "sum" else self.key


@dataclass(frozen=True, slots=True)
class Rule:
    """A set of guideline values from one document (for example NEMP 3.0, Table 4)."""

    id: str
    name: str
    document: str
    table: str
    page: str
    page_basis: str
    wa_status: str
    limits: tuple[Limit, ...]

    @property
    def citation(self) -> str:
        return f"{self.document}, {self.table}, {self.page_basis} {self.page}"


@dataclass(frozen=True, slots=True)
class Dataset:
    """All lab results and guideline rules available to the tools."""

    results: tuple[LabResult, ...]
    rules: Mapping[str, Rule]
    _rounds: dict[str, tuple[Round, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        grouped: dict[tuple[str, dt.date], list[LabResult]] = {}
        for result in self.results:
            grouped.setdefault((result.well, result.date), []).append(result)
        rounds: dict[str, list[Round]] = {}
        for (well, date), rows in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
            first = rows[0]
            by_analyte = {row.analyte: row for row in rows}
            rounds.setdefault(well, []).append(
                Round(well=well, date=date, lab_report=first.lab_report, file=first.file, results=by_analyte)
            )
        object.__setattr__(self, "_rounds", {well: tuple(items) for well, items in rounds.items()})

    @property
    def wells(self) -> tuple[str, ...]:
        return tuple(sorted(self._rounds))

    def resolve_well(self, well: str) -> str:
        """Return the well id as stored, matching case-insensitively. Raises EvidencelineError if unknown."""
        wanted = well.strip().upper()
        for known in self._rounds:
            if known.upper() == wanted:
                return known
        raise EvidencelineError(f"Unknown well {well!r}. Wells with data: {', '.join(self.wells)}.")

    def rounds(self, well: str) -> tuple[Round, ...]:
        return self._rounds[self.resolve_well(well)]

    def analytes(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for result in self.results:
            seen.setdefault(result.analyte, None)
        return tuple(seen)

    def resolve_rule(self, rule: str) -> Rule:
        key = rule.strip().lower()
        if key in self.rules:
            return self.rules[key]
        raise EvidencelineError(f"Unknown rule {rule!r}. Use one of: {', '.join(self.rules)}.")


def long_date(value: dt.date) -> str:
    """``2025-09-16`` becomes ``16 September 2025``."""
    return f"{value.day} {value.strftime('%B')} {value.year}"


def _read_lab_file(name: str, text: str) -> list[LabResult]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [col for col in _REQUIRED_COLUMNS if col not in (reader.fieldnames or [])]
    if missing:
        raise EvidencelineError(f"Lab file {name} is missing columns: {', '.join(missing)}.")
    rows: list[LabResult] = []
    for index, raw in enumerate(reader):
        row_number = index + 2  # the header is row 1, as in a spreadsheet
        reported = raw["result"].strip()
        unit = raw["unit"].strip()
        try:
            lor = to_ug_per_l(parse_decimal(raw["lor"]), unit)
            if reported.startswith("<"):
                value = None
                if to_ug_per_l(parse_decimal(reported[1:]), unit) != lor:
                    raise ValueError("a '<' result must equal the detection limit")
            else:
                value = to_ug_per_l(parse_decimal(reported), unit)
            date = dt.date.fromisoformat(raw["sample_date"].strip())
        except ValueError as exc:
            raise EvidencelineError(f"Lab file {name} row {row_number} could not be read: {exc}") from exc
        rows.append(
            LabResult(
                well=raw["well_id"].strip(),
                site=raw["site_id"].strip(),
                matrix=raw["matrix"].strip(),
                sample_id=raw["sample_id"].strip(),
                date=date,
                analyte=raw["analyte"].strip(),
                reported=reported,
                value=value,
                detection_limit=lor,
                lab_report=raw["lab_report_id"].strip(),
                file=name,
                row=row_number,
            )
        )
    return rows


def _parse_rules(payload: Mapping[str, Any]) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    for raw_rule in cast(list[dict[str, Any]], payload["rules"]):
        rule_id = str(raw_rule["id"])
        limits: list[Limit] = []
        for raw in cast(list[dict[str, Any]], raw_rule["limits"]):
            applies_to = str(raw["applies_to"])
            if applies_to not in ("single", "sum"):
                raise EvidencelineError(f"Rule {rule_id}: applies_to must be 'single' or 'sum', not {applies_to!r}.")
            limits.append(
                Limit(
                    rule_id=rule_id,
                    key=str(raw["key"]),
                    applies_to=applies_to,
                    members=tuple(str(m) for m in cast(list[str], raw["members"])),
                    value=to_ug_per_l(parse_decimal(str(raw["value"])), str(raw["unit"])),
                    scenario=str(raw["scenario"]),
                    note=str(raw["note"]),
                )
            )
        rules[rule_id] = Rule(
            id=rule_id,
            name=str(raw_rule["name"]),
            document=str(raw_rule["document"]),
            table=str(raw_rule["table"]),
            page=str(raw_rule["page"]),
            page_basis=str(raw_rule["page_basis"]),
            wa_status=str(raw_rule["wa_status"]),
            limits=tuple(limits),
        )
    return rules


def _lab_files(root: Traversable) -> Iterable[Traversable]:
    return sorted((item for item in root.iterdir() if item.name.endswith("_lab.csv")), key=lambda item: item.name)


def load_dataset(data_dir: Path | Traversable | None = None) -> Dataset:
    """Read every ``*_lab.csv`` file and ``guidelines.json`` from ``data_dir`` (default: packaged data)."""
    root: Traversable = data_dir if data_dir is not None else resources.files("evidenceline") / "data"
    results: list[LabResult] = []
    for item in _lab_files(root):
        results.extend(_read_lab_file(item.name, item.read_text(encoding="utf-8")))
    if not results:
        raise EvidencelineError(f"No lab files (*_lab.csv) found in {root}.")
    rules = _parse_rules(json.loads((root / "guidelines.json").read_text(encoding="utf-8")))
    return Dataset(results=tuple(results), rules=rules)


@cache
def default_dataset() -> Dataset:
    """The packaged dataset, loaded once."""
    return load_dataset()
