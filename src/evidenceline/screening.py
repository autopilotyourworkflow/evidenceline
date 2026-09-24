"""Compare one monitoring round with one guideline value. Deterministic and exact.

Two rules from the guidance are applied everywhere:

* A result equal to the limit is not above it (the comparison is strictly greater than).
* A limit is an investigation level. Being above it means "look further", not "unsafe" or "contaminated".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from evidenceline.dataset import LabResult, Limit, Round, Rule, long_date
from evidenceline.models import LimitScreen, ScreenStatus, SourceRef
from evidenceline.units import CANONICAL_UNIT, fmt, fmt_ug

EQUAL_IS_NOT_ABOVE = "A result equal to the limit is not above it."
INVESTIGATION_LEVEL = (
    "A guideline limit is an investigation level: a result above it means look further, "
    "not that the water is unsafe or the site is contaminated."
)
RULE_CHOICE = "Choosing which rule applies is the scientist's call. Evidenceline shows both and does not pick one."
FOOTNOTE_A = "PFOS only, PFHxS only, and the sum of the two"
"""PFAS NEMP 3.0 Table 4, footnote a (and Table 5, footnote a), quoted: what a value for the sum of PFOS and PFHxS
applies to."""


def member_of_sum_note(analyte: str) -> str:
    """Why a value for the sum of PFOS and PFHxS is also used for ``analyte`` on its own."""
    return (
        f"Under this rule the value for the sum of PFOS and PFHxS also applies to {analyte} on its own: Table 4, "
        f'footnote a says it means "{FOOTNOTE_A}".'
    )


def lab_source(result: LabResult) -> SourceRef:
    if result.value is None:
        return SourceRef(
            kind="detection limit",
            reference=f"{result.analyte} not detected, detection limit {fmt_ug(result.detection_limit)}, "
            f"{long_date(result.date)} ({result.lab_report}, {result.file} row {result.row})",
            value=fmt(result.detection_limit),
            analyte=result.analyte,
            date=result.date.isoformat(),
            lab_report=result.lab_report,
            file=result.file,
            rows=[result.row],
        )
    return SourceRef(
        kind="lab result",
        reference=result.describe(),
        value=fmt(result.value),
        analyte=result.analyte,
        date=result.date.isoformat(),
        lab_report=result.lab_report,
        file=result.file,
        rows=[result.row],
    )


def limit_source(rule: Rule, limit: Limit) -> SourceRef:
    return SourceRef(
        kind="guideline limit",
        reference=f"{rule.name}: {limit.label} {fmt_ug(limit.value)} ({rule.citation})",
        value=fmt(limit.value),
        analyte=limit.key,
        rule=rule.id,
        document=rule.document,
        table=rule.table,
        page=f"{rule.page_basis} {rule.page}",
    )


@dataclass(frozen=True, slots=True)
class Screen:
    """The outcome of comparing one round with one limit. ``above`` is None when it cannot be confirmed."""

    limit: Limit
    compared_value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    arithmetic: str
    above: bool | None
    status: ScreenStatus
    explanation: str
    results: tuple[LabResult, ...]
    alone: str | None = None
    """Set when one member of a sum is screened on its own against the sum's value (footnote a)."""

    def to_model(self, rule: Rule) -> LimitScreen:
        return LimitScreen(
            compared_quantity=f"{self.alone} on its own" if self.alone else self.limit.label,
            limit=fmt(self.limit.value),
            unit=CANONICAL_UNIT,
            compared_value=None if self.compared_value is None else fmt(self.compared_value),
            arithmetic=self.arithmetic,
            status=self.status,
            explanation=self.explanation,
            sources=[*(lab_source(r) for r in self.results), limit_source(rule, self.limit)],
        )


def _verdict(value: Decimal, limit: Limit) -> tuple[bool, str]:
    shown_limit = fmt_ug(limit.value)
    if value > limit.value:
        return True, f"{fmt_ug(value)} is above {shown_limit}."
    if value == limit.value:
        return False, f"{fmt_ug(value)} equals {shown_limit}, so it is not above it."
    return False, f"{fmt_ug(value)} is not above {shown_limit}."


def screen_limit(limit: Limit, round_: Round) -> Screen:
    """Compare ``round_`` with ``limit``, summing members when the limit applies to a sum."""
    members = [round_.results.get(name) for name in limit.members]
    present = [r for r in members if r is not None]
    if len(present) != len(members):
        missing = [name for name, r in zip(limit.members, members, strict=True) if r is None]
        return Screen(
            limit=limit,
            compared_value=None,
            low=None,
            high=None,
            arithmetic="",
            above=None,
            status="not analysed",
            explanation=f"No result for {', '.join(missing)} on {long_date(round_.date)}.",
            results=tuple(present),
        )

    detected = [r.value for r in present if r.value is not None]
    non_detects = [r for r in present if r.value is None]
    terms = [fmt(r.value) if r.value is not None else f"<{fmt(r.detection_limit)}" for r in present]

    if not non_detects:
        value = sum(detected, Decimal(0))
        arithmetic = f"{' + '.join(terms)} = {fmt(value)}" if len(terms) > 1 else terms[0]
        above, explanation = _verdict(value, limit)
        if len(terms) > 1:
            explanation = f"Sum {arithmetic} {CANONICAL_UNIT}. {explanation}"
        return Screen(
            limit,
            value,
            value,
            value,
            arithmetic,
            above,
            "above" if above else "not above",
            explanation,
            tuple(present),
        )

    # One or more members not detected: bound the compared value between "count as zero" and "count as the LOR".
    low = sum(detected, Decimal(0))
    high = low + sum((r.detection_limit for r in non_detects), Decimal(0))
    arithmetic = " + ".join(terms)
    names = ", ".join(r.analyte for r in non_detects)
    if high <= limit.value:
        explanation = (
            f"{names} not detected. Even counting a non-detect at its detection limit gives {fmt_ug(high)}, "
            f"which is not above {fmt_ug(limit.value)}."
        )
        return Screen(limit, None, low, high, arithmetic, False, "not above", explanation, tuple(present))
    if low > limit.value:
        explanation = (
            f"Counting non-detects ({names}) as zero still gives {fmt_ug(low)}, which is above {fmt_ug(limit.value)}."
        )
        return Screen(limit, None, low, high, arithmetic, True, "above", explanation, tuple(present))
    explanation = (
        f"Not confirmed: {names} not detected, and the detection limit is too high to tell whether the result is "
        f"above {fmt_ug(limit.value)}. How to count the non-detect is the scientist's call."
    )
    return Screen(limit, None, low, high, arithmetic, None, "not confirmed", explanation, tuple(present))


def screen_member(limit: Limit, analyte: str, round_: Round) -> Screen:
    """Compare one member of a sum (PFOS or PFHxS) on its own with the sum's value, as footnote a requires."""
    if limit.applies_to != "sum" or analyte not in limit.members:
        raise ValueError(f"{analyte} is not a member of the sum {limit.key}")
    screen = screen_limit(replace(limit, members=(analyte,)), round_)
    return replace(
        screen,
        limit=limit,
        explanation=f"{analyte} on its own (Table 4, footnote a): {screen.explanation}",
        alone=analyte,
    )


def screens_for_rule(rule: Rule, round_: Round) -> list[Screen]:
    """Every screen one rule asks for: each value as listed, then each member of a sum on its own (footnote a)."""
    screens = [screen_limit(limit, round_) for limit in rule.limits]
    screens.extend(
        screen_member(limit, member, round_)
        for limit in rule.limits
        if limit.applies_to == "sum"
        for member in limit.members
    )
    return screens


Op = Literal[">", ">=", "<", "<="]
"""A comparison between a value and a limit or threshold."""

NEGATED: dict[Op, Op] = {">": "<=", "<=": ">", "<": ">=", ">=": "<"}


def compare_bounds(low: Decimal, high: Decimal, op: Op, threshold: Decimal) -> bool | None:
    """Test ``value op threshold`` when the value is only known to lie in ``[low, high]``. None if undecided."""
    if op == ">":
        return True if low > threshold else False if high <= threshold else None
    if op == "<":
        return True if high < threshold else False if low >= threshold else None
    result = compare_bounds(low, high, NEGATED[op], threshold)
    return None if result is None else not result


def limits_for_analyte(rule: Rule, analyte: str) -> tuple[Limit, ...]:
    """Limits in ``rule`` that screen ``analyte`` (a single analyte or the PFOS+PFHxS sum key)."""
    return tuple(limit for limit in rule.limits if limit.key == analyte or analyte in limit.members)
