"""Typed results of the tidy tools. The MCP server publishes these as structured output schemas.

Concentrations are strings holding exact decimals (ug/L for water, mg/kg for soil), never floats. Row numbers are
the file's own line numbers, as a spreadsheet shows them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CheckName = Literal["sample ids", "units", "field duplicates", "holding times", "blanks", "LOR against criteria"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceRow(_Model):
    file: str
    row: int = Field(description="Line number in the file, as a spreadsheet shows it.")
    sample_id: str | None = None
    analyte: str | None = None
    detail: str


class ReviewItem(_Model):
    number: int = Field(description="1-based; use it with get_review_item.")
    check: CheckName
    title: str = Field(description="One line in plain English.")
    found: str = Field(description="What was found, with the numbers and the arithmetic.")
    samples: list[str] = Field(description="Sample ids this item is about.")
    evidence: list[EvidenceRow]
    rule: str = Field(description="The rule applied, in plain English.")
    source: str = Field(description="Where the rule comes from, quoted.")
    scientist_decides: str = Field(description="The judgement left to the scientist. The tool never makes it.")
    related_items: list[int] = Field(description="Other review items about the same samples.")


class SourceLine(_Model):
    file: str
    row: int
    text: str = Field(description="The line exactly as it is in the file.")


class ReviewItemDetail(_Model):
    site: str
    synthetic: str
    item: ReviewItem
    of_total: int = Field(description="How many review items the site has.")
    source_lines: list[SourceLine] = Field(description="Every evidence row, as written in its file.")
    note: str


class CheckRun(_Model):
    check: CheckName
    what_was_checked: str
    rule: str
    source: str
    checked: int = Field(description="How many results, samples, pairs or comparisons were checked.")
    review_items: list[int] = Field(description="Review item numbers this check raised (may be empty).")


class CheckedNotFlagged(_Model):
    check: CheckName
    what: str
    evidence: list[EvidenceRow] = Field(default_factory=list[EvidenceRow])


class NotChecked(_Model):
    check: CheckName
    what: str
    reason: str


class CountRow(_Model):
    matrix: Literal["soil", "water"]
    analyte: str
    unit: str
    results: int
    detected: int
    not_detected: int


class FileRead(_Model):
    file: str
    role: Literal["lab results", "chain of custody", "field sheet"]
    header_row: int
    data_rows: int
    date_example: str = Field(description="A sampling date exactly as this file writes it (read day first).")


class TidyRow(_Model):
    """One lab result, joined to its chain-of-custody and field-sheet rows where the sample id matches exactly."""

    sample_id: str = Field(description="The sample id as the lab wrote it.")
    id_status: str = Field(description="'matched', or why not, with the review item number.")
    lab_report: str
    matrix: Literal["soil", "water"]
    qa_type: str | None = Field(description="From the field sheet: primary, field_duplicate or a blank type.")
    sampled: str = Field(description="ISO date, with the time when the lab gave one.")
    analyte: str
    reported: str = Field(description="The result and unit exactly as reported, for example '38 ng/L' or '<0.01 μg/L'.")
    value: str | None = Field(description="Exact value in the unit below, or null when not detected.")
    lor: str
    unit: str = Field(description="ug/L for water, mg/kg for soil.")
    detected: bool
    extracted: str | None
    days_to_extraction: int | None
    lab_row: int
    coc_row: int | None
    field_row: int | None
    review_items: list[int]


class QaSettings(_Model):
    rpd_review_above_percent: str
    rpd_investigate_above_percent: str
    rpd_no_limit_below_lor_multiple: str
    blank_marginal_factor: str


class TidyResult(_Model):
    site: str
    synthetic: str
    summary: str
    files: list[FileRead]
    samples: int = Field(description="Lab samples with results.")
    results: int
    counts: list[CountRow] = Field(description="Results by matrix and analyte.")
    review_items: list[ReviewItem]
    checks: list[CheckRun] = Field(description="Every check that ran, what it looked at and what it raised.")
    checked_not_flagged: list[CheckedNotFlagged]
    not_checked: list[NotChecked]
    rows: list[TidyRow] = Field(description="The combined table: one row per lab result.")
    settings: QaSettings
    notes: list[str]
