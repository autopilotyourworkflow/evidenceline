"""The four Evidenceline tools as plain Python functions.

Each function takes an optional ``data`` argument so tests can pass their own dataset. Without it the packaged
synthetic site (well MB2 at FDS-01) is used. Bad input raises :class:`~evidenceline.errors.EvidencelineError` with a
message that says what is valid.
"""

from __future__ import annotations

import datetime as dt
import re

from evidenceline.checker import check_paragraph_text
from evidenceline.dataset import SUM_KEY, Dataset, Round, default_dataset, long_date
from evidenceline.errors import EvidencelineError
from evidenceline.models import (
    LabResultOut,
    LimitInfo,
    ParagraphCheck,
    RoundComparison,
    RuleResult,
    WellResults,
)
from evidenceline.screening import (
    EQUAL_IS_NOT_ABOVE,
    INVESTIGATION_LEVEL,
    RULE_CHOICE,
    limits_for_analyte,
    member_of_sum_note,
    screens_for_rule,
)
from evidenceline.textparse import SpanSet, find_dates
from evidenceline.units import CANONICAL_UNIT, fmt

_SUM_ALIASES = {
    "sum",
    "pfos+pfhxs",
    "pfhxs+pfos",
    "sumofpfosandpfhxs",
    "pfosandpfhxs",
    "sumofpfos+pfhxs",
}


def _data(data: Dataset | None) -> Dataset:
    return data if data is not None else default_dataset()


def _resolve_analyte(data: Dataset, analyte: str) -> str:
    compact = re.sub(r"\s+", "", analyte).lower()
    if compact in _SUM_ALIASES:
        return SUM_KEY
    for known in data.analytes():
        if known.lower() == analyte.strip().lower():
            return known
    raise EvidencelineError(
        f"Unknown analyte {analyte!r}. Known analytes: {', '.join(data.analytes())}, "
        f"or {SUM_KEY!r} for the sum of PFOS and PFHxS."
    )


def get_results(well: str, analyte: str | None = None, *, data: Dataset | None = None) -> WellResults:
    """All lab results for ``well`` (optionally one analyte), oldest first, each with its lab report, file and row."""
    ds = _data(data)
    rounds = ds.rounds(well)
    wanted = None if analyte is None or not analyte.strip() else _resolve_analyte(ds, analyte)
    if wanted == SUM_KEY:
        raise EvidencelineError("get_results returns lab rows; ask for 'PFOS' and 'PFHxS' separately, not the sum.")
    rows: list[LabResultOut] = []
    for round_ in rounds:
        for result in sorted(round_.results.values(), key=lambda r: r.row):
            if wanted is not None and result.analyte != wanted:
                continue
            rows.append(
                LabResultOut(
                    date=result.date.isoformat(),
                    analyte=result.analyte,
                    result=result.reported,
                    value=None if result.value is None else fmt(result.value),
                    detected=result.detected,
                    detection_limit=fmt(result.detection_limit),
                    unit=CANONICAL_UNIT,
                    lab_report=result.lab_report,
                    file=result.file,
                    row=result.row,
                )
            )
    first = next(iter(rounds[0].results.values()))
    return WellResults(
        well=rounds[0].well,
        site=first.site,
        matrix=first.matrix,
        unit=CANONICAL_UNIT,
        dates=[r.date.isoformat() for r in rounds],
        count=len(rows),
        results=rows,
        note=(
            "Synthetic data for a fictional site. A result shown as '<0.001' was not detected; the number is the "
            "detection limit. Row numbers count the header as row 1."
        ),
    )


def lookup_limit(analyte: str, rule: str, *, data: Dataset | None = None) -> LimitInfo:
    """The drinking-water guideline value for ``analyte`` under ``rule`` ("nemp-3.0" or "current")."""
    ds = _data(data)
    chosen = ds.resolve_rule(rule)
    key = _resolve_analyte(ds, analyte)
    limits = limits_for_analyte(chosen, key)
    if not limits:
        if key == SUM_KEY:
            raise EvidencelineError(
                f"Rule {chosen.id!r} has no value for the sum of PFOS and PFHxS: it screens PFOS and PFHxS "
                "separately. Ask for 'PFOS' or 'PFHxS'."
            )
        available = ", ".join(limit.label for limit in chosen.limits)
        raise EvidencelineError(
            f"Rule {chosen.id!r} has no drinking-water value for {key}. It has values for: {available}."
        )
    limit = limits[0]
    caveats = [EQUAL_IS_NOT_ABOVE, INVESTIGATION_LEVEL]
    compared = limit.label
    if limit.applies_to == "sum" and key != SUM_KEY:
        # Footnote a: the sum value also applies to each member alone, so the quantity to compare is the one asked for.
        caveats.insert(0, member_of_sum_note(key))
        compared = f"{key} on its own"
    return LimitInfo(
        rule=chosen.id,
        rule_name=chosen.name,
        analyte=key,
        applies_to=limit.applies_to,
        compared_quantity=compared,
        value=fmt(limit.value),
        unit=CANONICAL_UNIT,
        scenario=limit.scenario,
        source_document=chosen.document,
        table=chosen.table,
        page=chosen.page,
        page_basis=chosen.page_basis,
        wa_status=chosen.wa_status,
        note=limit.note,
        caveats=caveats,
    )


def _parse_round_date(date: str) -> tuple[int, int, int | None]:
    text = date.strip()
    try:
        exact = dt.date.fromisoformat(text)
    except ValueError:
        pass
    else:
        return exact.year, exact.month, exact.day
    found, unclear = find_dates(text, SpanSet())
    if len(found) == 1 and not unclear and found[0].text.strip(" .,") == text.strip(" .,"):
        mention = found[0]
        return mention.year, mention.month, mention.day
    raise EvidencelineError(
        f"Could not read the date {date!r}. Use a form such as '2025-09-16', '16 September 2025' or 'Sep 2025'."
    )


def _find_round(ds: Dataset, well: str, date: str) -> Round:
    rounds = ds.rounds(well)
    year, month, day = _parse_round_date(date)
    matches = [
        r for r in rounds if r.date.year == year and r.date.month == month and (day is None or r.date.day == day)
    ]
    available = ", ".join(f"{long_date(r.date)} ({r.date.isoformat()})" for r in rounds)
    if not matches:
        raise EvidencelineError(f"No {rounds[0].well} round on {date!r}. Rounds with data: {available}.")
    if len(matches) > 1:
        raise EvidencelineError(f"{date!r} matches more than one round. Give the day. Rounds with data: {available}.")
    return matches[0]


def compare_rules(well: str, date: str, *, data: Dataset | None = None) -> RoundComparison:
    """Screen one monitoring round under every rule, with the arithmetic shown. Never picks a rule."""
    ds = _data(data)
    round_ = _find_round(ds, well, date)
    results: list[RuleResult] = []
    for rule in ds.rules.values():
        screens = screens_for_rule(rule, round_)
        statuses = {s.above for s in screens if s.status != "not analysed"}
        overall = "above" if True in statuses else "not above" if statuses == {False} else "not confirmed"
        results.append(
            RuleResult(
                rule=rule.id,
                rule_name=rule.name,
                citation=rule.citation,
                wa_status=rule.wa_status,
                overall=overall,
                screens=[s.to_model(rule) for s in screens],
            )
        )
    agree = len({r.overall for r in results}) == 1
    notes = [RULE_CHOICE, EQUAL_IS_NOT_ABOVE, INVESTIGATION_LEVEL]
    if not agree:
        verdicts = "; ".join(f"{r.rule}: {r.overall}" for r in results)
        notes.insert(
            0,
            f"The rules give different answers for this round ({verdicts}). The lab values are the same in each "
            "rule; only the limit and how it is applied differ.",
        )
    return RoundComparison(
        well=round_.well,
        date=round_.date.isoformat(),
        lab_report=round_.lab_report,
        file=round_.file,
        rules=results,
        rules_agree=agree,
        notes=notes,
    )


def check_paragraph(text: str, well: str, *, data: Dataset | None = None) -> ParagraphCheck:
    """Check every number and claim in ``text`` against the lab data and guideline values for ``well``."""
    return check_paragraph_text(_data(data), text, well)
