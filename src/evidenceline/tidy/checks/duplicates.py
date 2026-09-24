"""Field duplicate pairs: relative percent difference (RPD) with the LOR-dependent rule.

Pairs come from the field sheet (qa_type ``field_duplicate`` and its ``parent_sample``), because the lab does not
know which samples are blind duplicates. Values are compared after unit conversion. A non-detect takes its LOR.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from evidenceline.tidy import sources
from evidenceline.tidy.arith import rpd_exceeds, rpd_places, rpd_shown, rpd_working
from evidenceline.tidy.config import QaConfig
from evidenceline.tidy.findings import Draft, Outcome, Passed, Skipped, field_evidence, join_words, lab_evidence
from evidenceline.tidy.records import FieldRow, LabRow
from evidenceline.tidy.site import SiteData
from evidenceline.units import fmt

Verdict = Literal["both not detected", "no limit", "acceptable", "review", "investigate"]


@dataclass(frozen=True, slots=True)
class PairResult:
    analyte: str
    primary: LabRow
    duplicate: LabRow
    verdict: Verdict
    places: int = 1
    """Decimal places shown, so an RPD just above a threshold never displays as equal to it."""

    @property
    def a(self) -> Decimal:
        return self.primary.value if self.primary.value is not None else self.primary.lor

    @property
    def b(self) -> Decimal:
        return self.duplicate.value if self.duplicate.value is not None else self.duplicate.lor

    @property
    def lor(self) -> Decimal:
        return max(self.primary.lor, self.duplicate.lor)

    @property
    def rpd(self) -> Decimal | None:
        return None if self.verdict == "both not detected" else rpd_shown(self.a, self.b, self.places)

    @property
    def mean(self) -> Decimal:
        return (self.a + self.b) / 2

    def described(self) -> str:
        """``Cadmium 40.0% (QC1 <0.4, its LOR used)``."""
        subs = [row for row in (self.primary, self.duplicate) if not row.detected]
        note = "; ".join(f"{row.sample_id} {row.shown_canonical()}, its LOR used" for row in subs)
        return f"{self.analyte} {self.rpd}%" + (f" ({note})" if note else "")


def rule_text(config: QaConfig) -> str:
    return (
        f"RPD = |a - b| / ((a + b) / 2) x 100, worked out after converting both results to one unit. A non-detect "
        f"takes its LOR; when both are non-detects no RPD is worked out. When the pair mean is below "
        f"{fmt(config.rpd_no_limit_below_lor_multiple)} x the LOR, the RPD is shown with no limit applied. Otherwise "
        f"an RPD above {fmt(config.rpd_review_above)}% is flagged for review, and above "
        f"{fmt(config.rpd_investigate_above)}% the flag says investigate. These thresholds are settings."
    )


def compare_pair(primary: LabRow, duplicate: LabRow, config: QaConfig) -> PairResult:
    """Classify one analyte in one duplicate pair."""
    if not primary.detected and not duplicate.detected:
        return PairResult(primary.analyte, primary, duplicate, "both not detected")
    result = PairResult(primary.analyte, primary, duplicate, "acceptable")
    a, b = result.a, result.b
    if a + b < 2 * config.rpd_no_limit_below_lor_multiple * result.lor:
        verdict: Verdict = "no limit"
    elif rpd_exceeds(a, b, config.rpd_investigate_above):
        verdict = "investigate"
    elif rpd_exceeds(a, b, config.rpd_review_above):
        verdict = "review"
    else:
        verdict = "acceptable"
    threshold = {"investigate": config.rpd_investigate_above, "review": config.rpd_review_above}.get(verdict)
    places = 1 if threshold is None else rpd_places(a, b, threshold)
    return PairResult(primary.analyte, primary, duplicate, verdict, places)


def duplicate_pairs(site: SiteData) -> list[FieldRow]:
    return [row for row in site.field if row.qa_type == "field_duplicate"]


def pair_results(site: SiteData, dup: FieldRow, config: QaConfig) -> list[PairResult] | None:
    """Every analyte reported for both samples of a pair, or None if either sample has no lab results."""
    lab = site.lab_samples()
    if dup.sample_id not in lab or dup.parent_sample not in lab:
        return None
    duplicate = {row.analyte: row for row in lab[dup.sample_id]}
    return [
        compare_pair(row, duplicate[row.analyte], config) for row in lab[dup.parent_sample] if row.analyte in duplicate
    ]


def unpaired_analytes(site: SiteData, dup: FieldRow) -> list[Skipped]:
    """Analytes reported for only one sample of a pair: no RPD can be worked out, and that is said, not dropped."""
    lab = site.lab_samples()
    parent = {row.analyte: row for row in lab.get(dup.parent_sample, [])}
    duplicate = {row.analyte: row for row in lab.get(dup.sample_id, [])}
    skipped: list[Skipped] = []
    for has, lacks, rows, other in (
        (dup.parent_sample, dup.sample_id, parent, duplicate),
        (dup.sample_id, dup.parent_sample, duplicate, parent),
    ):
        for analyte, row in rows.items():
            if analyte not in other:
                skipped.append(
                    Skipped(
                        check="field duplicates",
                        what=f"{dup.parent_sample} and {dup.sample_id}: {analyte}",
                        reason=(
                            f"{analyte} is reported for {has} (lab file row {row.row}) but not for {lacks}, so no RPD "
                            "was worked out for it."
                        ),
                    )
                )
    return skipped


def _flag_text(result: PairResult, config: QaConfig) -> str:
    limit = config.rpd_investigate_above if result.verdict == "investigate" else config.rpd_review_above
    unit = result.primary.canonical_unit
    word = "investigate" if result.verdict == "investigate" else "review"
    subs = [row for row in (result.primary, result.duplicate) if not row.detected]
    sub_note = "".join(f" {row.sample_id} was below its LOR, so its LOR ({fmt(row.lor)}) was used." for row in subs)
    return (
        f"{result.analyte}: {result.primary.sample_id} {result.primary.shown_both()} and {result.duplicate.sample_id} "
        f"{result.duplicate.shown_both()}.{sub_note} RPD = {rpd_working(result.a, result.b, result.places)}. The "
        "pair mean "
        f"({fmt(result.mean)} {unit}) is at least {fmt(config.rpd_no_limit_below_lor_multiple)} x the LOR "
        f"({fmt(config.rpd_no_limit_below_lor_multiple)} x {fmt(result.lor)} = "
        f"{fmt(config.rpd_no_limit_below_lor_multiple * result.lor)} {unit}), so the limit applies: {result.rpd}% is "
        f"above {fmt(limit)}% ({word})."
    )


def _draft(dup: FieldRow, flagged: list[PairResult], config: QaConfig) -> Draft:
    parent = dup.parent_sample
    worst = [f"{r.analyte} RPD {r.rpd}%" for r in flagged]
    top = config.rpd_review_above if any(r.verdict == "review" for r in flagged) else config.rpd_investigate_above
    evidence = [field_evidence(dup, f"{dup.sample_id}, blind field duplicate of {parent}")]
    for result in flagged:
        evidence += [lab_evidence(result.primary), lab_evidence(result.duplicate)]
    found = f"{dup.sample_id} is a blind field duplicate of {parent} (field sheet row {dup.row}). " + " ".join(
        _flag_text(result, config) for result in flagged
    )
    return Draft(
        check="field duplicates",
        title=f"Field duplicate pair {parent} and {dup.sample_id}: {join_words(worst)}, above {fmt(top)}%",
        found=found,
        evidence=tuple(evidence),
        rule=rule_text(config),
        source=sources.DUPLICATES,
        scientist_decides=(
            "The scientist decides the likely cause (for example uneven material in the sample, or a laboratory "
            "cause) and how the pair is reported. The tool does not choose which result to use or reject either. "
            + sources.REPORT_HIGHEST
        ),
        samples=(parent, dup.sample_id),
    )


def _passed(dup: FieldRow, results: list[PairResult], config: QaConfig) -> list[Passed]:
    pair = f"{dup.parent_sample} and {dup.sample_id}"
    converted = any(r.primary.unit.key != r.duplicate.unit.key for r in results)
    after = " (after unit conversion)" if converted else ""
    groups: list[tuple[Verdict, str]] = [
        ("no limit", f"pair mean below {fmt(config.rpd_no_limit_below_lor_multiple)} x the LOR, so no limit applied"),
        ("acceptable", f"at or below {fmt(config.rpd_review_above)}%{after}"),
    ]
    passed: list[Passed] = []
    for verdict, label in groups:
        chosen = [r for r in results if r.verdict == verdict]
        if chosen:
            listed = join_words([r.described() for r in chosen])
            evidence = tuple(lab_evidence(row) for r in chosen for row in (r.primary, r.duplicate))
            passed.append(Passed(check="field duplicates", what=f"{pair}, {label}: {listed}.", evidence=evidence))
    both = [r.analyte for r in results if r.verdict == "both not detected"]
    if both:
        passed.append(
            Passed(check="field duplicates", what=f"{pair}: {join_words(both)} below the LOR in both, so no RPD.")
        )
    return passed


def check_duplicates(site: SiteData, config: QaConfig) -> Outcome:
    drafts: list[Draft] = []
    passed: list[Passed] = []
    skipped: list[Skipped] = []
    compared = 0
    for dup in duplicate_pairs(site):
        results = pair_results(site, dup, config)
        if results is None:
            skipped.append(
                Skipped(
                    check="field duplicates",
                    what=f"{dup.parent_sample} and {dup.sample_id}",
                    reason="One of the pair has no lab results under the id on the field sheet.",
                )
            )
            continue
        skipped += unpaired_analytes(site, dup)
        compared += len(results)
        flagged = [r for r in results if r.verdict in ("review", "investigate")]
        if flagged:
            drafts.append(_draft(dup, flagged, config))
        passed += _passed(dup, [r for r in results if r not in flagged], config)
    pairs = len(duplicate_pairs(site))
    return Outcome(
        check="field duplicates",
        what_was_checked=f"{pairs} field duplicate pairs from the field sheet, {compared} analyte comparisons.",
        rule=rule_text(config),
        source=sources.DUPLICATES,
        checked=compared,
        drafts=tuple(drafts),
        passed=tuple(passed),
        skipped=tuple(skipped),
    )
