"""Typed results returned by the tools. The MCP server publishes these as structured output schemas.

Concentrations are strings holding exact decimals in ug/L (for example ``"0.038"``), never floats.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


SourceKind = Literal["lab result", "detection limit", "computed sum", "computed difference", "guideline limit"]


class SourceRef(_Model):
    """Where a value came from: a lab row, a guideline table, or arithmetic on lab rows."""

    kind: SourceKind
    reference: str = Field(description="One line a person can follow to find the value.")
    value: str = Field(description="Exact value in ug/L.")
    unit: str = "ug/L"
    analyte: str | None = None
    date: str | None = Field(default=None, description="Sample date, ISO format.")
    lab_report: str | None = None
    file: str | None = None
    rows: list[int] = Field(default_factory=list[int], description="Row numbers in the lab file (header is row 1).")
    rule: str | None = None
    document: str | None = None
    table: str | None = None
    page: str | None = None


# get_results ---------------------------------------------------------------------------------------------------


class LabResultOut(_Model):
    date: str = Field(description="Sample date, ISO format.")
    analyte: str
    result: str = Field(description="Result exactly as the lab reported it, for example '0.038' or '<0.001'.")
    value: str | None = Field(description="Exact value in ug/L, or null when not detected.")
    detected: bool
    detection_limit: str
    unit: str
    lab_report: str
    file: str
    row: int = Field(description="Row number in the lab file (header is row 1).")


class WellResults(_Model):
    well: str
    site: str
    matrix: str
    unit: str
    dates: list[str] = Field(description="Sample dates with results, oldest first.")
    count: int
    results: list[LabResultOut]
    note: str


# lookup_limit --------------------------------------------------------------------------------------------------


class LimitInfo(_Model):
    rule: str
    rule_name: str
    analyte: str = Field(description="The analyte you asked about.")
    applies_to: Literal["single", "sum"] = Field(
        description="What the rule sets the value for: 'sum' means PFOS and PFHxS added together (under NEMP 3.0, "
        "Table 4, footnote a, the same value also applies to PFOS on its own and PFHxS on its own); 'single' means "
        "one analyte."
    )
    compared_quantity: str = Field(
        description="What to compare with the value for the analyte asked about: 'sum of PFOS and PFHxS', or one "
        "analyte such as 'PFOS', or 'PFOS on its own' when a sum value also applies to PFOS alone."
    )
    value: str
    unit: str
    scenario: str
    source_document: str
    table: str
    page: str
    page_basis: str = Field(description="'PDF page' or 'printed page'.")
    wa_status: str
    note: str = Field(description="One line in plain English.")
    caveats: list[str]


# compare_rules -------------------------------------------------------------------------------------------------

ScreenStatus = Literal["above", "not above", "not confirmed", "not analysed"]


class LimitScreen(_Model):
    compared_quantity: str
    limit: str
    unit: str
    compared_value: str | None
    arithmetic: str = Field(description="How the compared value was worked out, for example '0.038 + 0.019 = 0.057'.")
    status: ScreenStatus
    explanation: str
    sources: list[SourceRef]


class RuleResult(_Model):
    rule: str
    rule_name: str
    citation: str
    wa_status: str
    overall: Literal["above", "not above", "not confirmed"]
    screens: list[LimitScreen]


class RoundComparison(_Model):
    well: str
    date: str
    lab_report: str
    file: str
    rules: list[RuleResult]
    rules_agree: bool
    notes: list[str]


# check_paragraph -----------------------------------------------------------------------------------------------

CheckStatus = Literal["consistent", "inconsistent", "untraced", "depends_on_rule", "needs_judgement"]


class RuleOutcome(_Model):
    rule: str
    holds: bool | None = Field(description="True if the claim holds under this rule, false if not, null if unknown.")
    explanation: str


class CheckedItem(_Model):
    sentence: int = Field(description="1-based sentence number.")
    quote: str = Field(description="The words or number that were checked.")
    kind: Literal["number", "change", "guideline", "detection"]
    status: CheckStatus
    explanation: str
    rule_outcomes: list[RuleOutcome] = Field(default_factory=list[RuleOutcome])
    sources: list[SourceRef] = Field(default_factory=list[SourceRef])


class NotCheckedItem(_Model):
    sentence: int
    quote: str
    reason: str


class ParagraphCheck(_Model):
    well: str
    summary: str
    scope: str = Field(description="What kinds of statement this checker covers.")
    sentences: list[str]
    checked: list[CheckedItem]
    not_checked: list[NotCheckedItem]
    needs_attention: int = Field(description="Checked items whose status is not 'consistent'.")
    notes: list[str]
