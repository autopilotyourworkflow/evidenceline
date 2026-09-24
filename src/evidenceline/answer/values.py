"""Verified drinking-water guideline values for a question, under BOTH rules, from ``guidelines.json`` via
:func:`evidenceline.core.lookup_limit`. Never read from extracted text; never one rule without the other.

Each value carries the verified note from ``guidelines.json``, except when a question names PFOS or PFHxS and the
rule's value is for their sum: then the note is one sentence saying the sum value also applies to that analyte on
its own, followed by the footnote it rests on (NEMP 3.0 Table 4, footnote a: "PFOS only, PFHxS only, and the sum of
the two"). The verified note would say the same thing a second time, so it is not added. Such a value names the
same quantity to compare as ``lookup_limit`` does ("PFOS on its own"), and its note says the rule sets it for the
sum and it also applies to the analyte on its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from evidenceline import core
from evidenceline.answer.models import GuidelineValue
from evidenceline.dataset import SUM_KEY, Dataset, Rule, default_dataset
from evidenceline.errors import EvidencelineError
from evidenceline.models import LimitInfo
from evidenceline.screening import FOOTNOTE_A
from evidenceline.units import CANONICAL_UNIT


def guideline_values(analytes: Sequence[str], *, data: Dataset | None = None) -> list[GuidelineValue]:
    """One entry per analyte and rule, numbered G1, G2 ... in that order.

    A rule with no value for an analyte (PFBS under NEMP 3.0, the sum under the current values) gets an entry
    with ``available`` false and the reason, so the absence is shown rather than skipped.
    """
    ds = data if data is not None else default_dataset()
    values: list[GuidelineValue] = []
    for analyte in analytes:
        for rule in ds.rules.values():
            marker = f"G{len(values) + 1}"
            try:
                info = core.lookup_limit(analyte, rule.id, data=ds)
            except EvidencelineError as exc:
                values.append(
                    GuidelineValue(
                        marker=marker,
                        rule=rule.id,
                        rule_name=rule.name,
                        analyte=analyte,
                        available=False,
                        compared_quantity=None,
                        value=None,
                        unit=None,
                        source_document=rule.document,
                        table=rule.table,
                        page=rule.page,
                        page_basis=rule.page_basis,
                        wa_status=rule.wa_status,
                        note=_no_value_note(ds, rule, analyte, exc),
                    )
                )
                continue
            values.append(
                GuidelineValue(
                    marker=marker,
                    rule=info.rule,
                    rule_name=info.rule_name,
                    analyte=analyte,
                    available=True,
                    compared_quantity=info.compared_quantity,
                    value=info.value,
                    unit=CANONICAL_UNIT,
                    source_document=info.source_document,
                    table=info.table,
                    page=info.page,
                    page_basis=info.page_basis,
                    wa_status=info.wa_status,
                    note=_note(info),
                )
            )
    return values


def member_note(info: LimitInfo) -> str:
    """One sentence and the footnote quote, for a sum value used for PFOS or PFHxS on its own.

    For example: 'Under this rule the value for the sum of PFOS and PFHxS, 0.07 ug/L, also applies to PFOS on its
    own. Table 4, footnote a: "PFOS only, PFHxS only, and the sum of the two".'
    """
    return (
        f"Under this rule the value for the sum of PFOS and PFHxS, {info.value} {info.unit}, also applies to "
        f'{info.analyte} on its own. {info.table}, footnote a: "{FOOTNOTE_A}".'
    )


def _note(info: LimitInfo) -> str:
    """The verified note from the data file, or :func:`member_note` when a sum value is used for one member."""
    if info.applies_to == "sum" and info.analyte != SUM_KEY:
        return member_note(info)
    return info.note


def _no_value_note(ds: Dataset, rule: Rule, analyte: str, error: EvidencelineError) -> str:
    """Why ``rule`` has no value for ``analyte``, in plain words for the website (the tool's error message names the
    rule id and tells a caller what to ask for instead)."""
    if analyte == SUM_KEY:
        return "This rule screens PFOS and PFHxS separately, not as a sum."
    if analyte in ds.analytes():
        labels = ", ".join(limit.label for limit in rule.limits)
        return f"This rule's drinking-water values are for: {labels}."
    return str(error)


def describe(value: GuidelineValue) -> str:
    """One line for the prompt, holding every number the answer may take from this value."""
    where = f"{value.source_document}, {value.table}, {value.page_basis} {value.page}"
    if not value.available:
        return f"[{value.marker}] {value.rule_name}: no value for {value.analyte}. {value.note} Source: {where}."
    return (
        f"[{value.marker}] {value.rule_name}: {value.compared_quantity} {value.value} {value.unit} "
        f"(drinking water). {value.note} Source: {where}. WA status: {value.wa_status}"
    )
