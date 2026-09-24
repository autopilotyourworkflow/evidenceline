"""Holding times: days from sampling to extraction, against the recommended maximum for the analyte and matrix."""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass

from evidenceline.dataset import long_date
from evidenceline.tidy import sources
from evidenceline.tidy.findings import Draft, Outcome, Passed, Skipped, join_words, lab_evidence, plural
from evidenceline.tidy.records import LabRow
from evidenceline.tidy.site import SiteData
from evidenceline.tidy.units import Matrix

PFAS = frozenset(
    {"PFOS", "PFHxS", "PFOA", "PFHxA", "PFBS", "PFBA", "PFPeA", "PFHpA", "PFNA", "PFDA", "PFPeS", "PFHpS"}
    | {"4:2 FTS", "6:2 FTS", "8:2 FTS"}
)
METALS_SIX_MONTHS = frozenset({"Arsenic", "Cadmium", "Chromium (total)", "Copper", "Lead", "Nickel", "Zinc"})


@dataclass(frozen=True, slots=True)
class HoldingRule:
    name: str
    matrix: Matrix
    analytes: frozenset[str]
    days: int = 0
    months: int = 0
    basis: str = ""

    @property
    def limit_text(self) -> str:
        return plural(self.days, "day") if self.days else plural(self.months, "month")

    def deadline(self, sampled: dt.date) -> dt.date:
        if self.days:
            return sampled + dt.timedelta(days=self.days)
        month_index = sampled.month - 1 + self.months
        year, month = sampled.year + month_index // 12, month_index % 12 + 1
        return dt.date(year, month, min(sampled.day, calendar.monthrange(year, month)[1]))


RULES: tuple[HoldingRule, ...] = (
    HoldingRule("Mercury in soil", "soil", frozenset({"Mercury"}), days=28, basis="NEPM B3 Table 1"),
    HoldingRule("Other metals in soil", "soil", METALS_SIX_MONTHS, months=6, basis="NEPM B3 Table 1"),
    HoldingRule("PFAS in soil", "soil", PFAS, days=28, basis="laboratory guidance, trace level (shorter of 60 and 28)"),
    HoldingRule("PFAS in water", "water", PFAS, days=14, basis="laboratory guidance, standard (shorter of 14 and 28)"),
)

RULE = (
    "Days from the sampling date to the extraction date, compared with the recommended maximum for the analyte and "
    "matrix: mercury in soil 28 days, other metals in soil 6 months, PFAS in soil 28 days and PFAS in water 14 days. "
    "Extraction on the last allowed day is within the time. An extraction date before the sampling date is "
    "flagged, because one of the two dates must be wrong."
)


def rule_for(row: LabRow) -> HoldingRule | None:
    return next((rule for rule in RULES if rule.matrix == row.matrix and row.analyte in rule.analytes), None)


def days_held(row: LabRow) -> int | None:
    return None if row.extracted is None else (row.extracted - row.sampled.date).days


def _draft(sample: str, breaches: list[tuple[LabRow, HoldingRule]], unmatched: Collection[str]) -> Draft:
    parts: list[str] = []
    for row, rule in breaches:
        held = days_held(row) or 0
        over = (row.extracted - rule.deadline(row.sampled.date)).days if row.extracted else 0
        parts.append(
            f"{row.analyte} in {sample} (lab file row {row.row}, result {row.shown_both()}) was sampled on "
            f"{long_date(row.sampled.date)} and extracted on {long_date(row.extracted or row.sampled.date)}: "
            f"{plural(held, 'day')} later. The recommended maximum for {rule.name.lower()} is {rule.limit_text} to "
            f"extraction ({rule.basis}), so it is {plural(over, 'day')} over."
        )
    comments = sorted({row.comment for row, _ in breaches if row.comment})
    if comments:
        parts.append(f"The lab comment reads: {join_words([repr(c) for c in comments])}.")
    if sample in unmatched:
        parts.append(f"{sample} is the lab's id and is not yet matched to a chain-of-custody id (see the id item).")
    first = breaches[0]
    analytes = join_words([row.analyte for row, _ in breaches])
    held = days_held(first[0]) or 0
    return Draft(
        check="holding times",
        title=f"{analytes} in {sample} extracted after the holding time ({held} days; limit {first[1].limit_text})",
        found=" ".join(parts),
        evidence=tuple(
            lab_evidence(
                row,
                f"{sample}, {row.analyte}, sampled {row.sampled.written}, extracted "
                f"{long_date(row.extracted or row.sampled.date)}",
            )
            for row, _ in breaches
        ),
        rule=RULE,
        source=sources.HOLDING,
        scientist_decides=(
            f"The scientist decides whether to accept, qualify or re-sample the {analytes.lower()} result. For "
            f"context, {sources.METALS_TOLERANCE}"
        ),
        samples=(sample,),
    )


def _before_sampling_draft(sample: str, rows: list[LabRow], unmatched: Collection[str]) -> Draft:
    parts = [
        f"{row.analyte} in {sample} (lab file row {row.row}) was sampled on {long_date(row.sampled.date)} and "
        f"extracted on {long_date(row.extracted or row.sampled.date)}: {plural(-(days_held(row) or 0), 'day')} "
        "before it was sampled, which cannot happen. The holding time was not worked out."
        for row in rows
    ]
    if sample in unmatched:
        parts.append(f"{sample} is the lab's id and is not yet matched to a chain-of-custody id (see the id item).")
    analytes = join_words([row.analyte for row in rows])
    return Draft(
        check="holding times",
        title=f"{analytes} in {sample}: the extraction date is before the sampling date",
        found=" ".join(parts),
        evidence=tuple(
            lab_evidence(
                row,
                f"{sample}, {row.analyte}, sampled {row.sampled.written}, extracted "
                f"{long_date(row.extracted or row.sampled.date)}",
            )
            for row in rows
        ),
        rule=RULE,
        source=sources.HOLDING,
        scientist_decides=(
            f"The scientist decides which date is wrong (sampling or extraction) for the {analytes.lower()} result, "
            "for example from the chain of custody and the lab's sample receipt record, and asks the lab to correct "
            "the report."
        ),
        samples=(sample,),
    )


def check_holding(site: SiteData, unmatched: Collection[str]) -> Outcome:
    breaches: dict[str, list[tuple[LabRow, HoldingRule]]] = {}
    before_sampling: dict[str, list[LabRow]] = {}
    within: dict[str, list[tuple[LabRow, int]]] = {}
    skipped: list[Skipped] = []
    no_rule: dict[str, list[LabRow]] = {}
    checked = 0
    for row in site.lab:
        rule = rule_for(row)
        held = days_held(row)
        if rule is None:
            no_rule.setdefault(f"{row.analyte} in {row.matrix}", []).append(row)
            continue
        if held is None or row.extracted is None:
            skipped.append(Skipped("holding times", f"{row.sample_id} {row.analyte}", "No extraction date."))
            continue
        checked += 1
        if held < 0:
            before_sampling.setdefault(row.sample_id, []).append(row)
        elif row.extracted > rule.deadline(row.sampled.date):
            breaches.setdefault(row.sample_id, []).append((row, rule))
        else:
            within.setdefault(rule.name, []).append((row, held))
    for what, rows in no_rule.items():
        skipped.append(
            Skipped(
                "holding times",
                f"{what} ({plural(len(rows), 'result')})",
                "No holding time is loaded for this analyte and matrix, so it was not checked.",
            )
        )
    passed: list[Passed] = []
    for rule in RULES:
        rows = within.get(rule.name, [])
        if not rows:
            continue
        longest_row, longest = max(rows, key=lambda item: item[1])
        at_limit = rule.days and longest == rule.days
        edge = ", at the limit and not over it" if at_limit else ""
        other = "other " if any(b_rule.name == rule.name for items in breaches.values() for _, b_rule in items) else ""
        passed.append(
            Passed(
                check="holding times",
                what=(
                    f"{rule.name} ({rule.limit_text}): {plural(len(rows), other + 'result')} within the time; the "
                    f"longest is {longest_row.sample_id} {longest_row.analyte} at {plural(longest, 'day')}{edge}."
                ),
                evidence=(lab_evidence(longest_row),),
            )
        )
    return Outcome(
        check="holding times",
        what_was_checked=f"Days from sampling to extraction for {plural(checked, 'result')}.",
        rule=RULE,
        source=sources.HOLDING,
        checked=checked,
        drafts=(
            *(_before_sampling_draft(sample, rows, unmatched) for sample, rows in before_sampling.items()),
            *(_draft(sample, items, unmatched) for sample, items in breaches.items()),
        ),
        passed=tuple(passed),
        skipped=tuple(skipped),
    )
