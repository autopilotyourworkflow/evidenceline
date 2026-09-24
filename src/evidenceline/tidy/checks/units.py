"""Units within each matrix. Spelling variants of one unit (ug/L, µg/L, μg/L) are not an inconsistency; a result in
a different unit (ng/L among ug/L) is flagged, and every result is converted exactly either way."""

from __future__ import annotations

from collections import Counter

from evidenceline.tidy import sources
from evidenceline.tidy.arith import rpd_shown
from evidenceline.tidy.checks.duplicates import duplicate_pairs
from evidenceline.tidy.findings import Draft, Outcome, Passed, join_words, lab_evidence, plural
from evidenceline.tidy.records import LabRow
from evidenceline.tidy.site import SiteData
from evidenceline.tidy.units import CANONICAL, Matrix, spelling_key
from evidenceline.units import fmt

RULE = (
    "Within one matrix every result must be in one unit before anything is compared or an RPD is worked out. "
    "Results are converted exactly to ug/L (water) or mg/kg (soil) by moving the decimal point, and the value and "
    "unit as reported are kept beside the converted value. Different spellings of one unit are not flagged."
)

_NAMES = {"µ": "micro sign", "μ": "Greek mu"}


def _spelling_note(written: str) -> str:
    for char, name in _NAMES.items():
        if char in written:
            return f"{written} ({name})"
    return written


def _duplicate_note(site: SiteData, sample: str, rows: list[LabRow]) -> str:
    """How far off an RPD with its duplicate partner would be without conversion."""
    lab = site.lab_samples()
    for dup in duplicate_pairs(site):
        if sample not in (dup.sample_id, dup.parent_sample):
            continue
        partner = dup.parent_sample if sample == dup.sample_id else dup.sample_id
        other = {row.analyte: row for row in lab.get(partner, [])}
        for row in rows:
            match = other.get(row.analyte)
            if match is None or row.value is None or match.value is None or match.unit.key == row.unit.key:
                continue
            raw_a, raw_b = row.value.scaleb(-row.unit.exponent), match.value.scaleb(-match.unit.exponent)
            return (
                f" {sample} has a field duplicate, {partner}, reported in {match.unit.written}. Without the "
                f"conversion, {row.analyte} would compare {fmt(raw_a)} with {fmt(raw_b)} and give a false RPD of "
                f"{rpd_shown(raw_a, raw_b)}% instead of {rpd_shown(row.value, match.value)}%."
            )
    return ""


def _draft(site: SiteData, sample: str, rows: list[LabRow], majority: str, others: int) -> Draft:
    matrix = rows[0].matrix
    conversions = join_words([f"{row.analyte} {row.shown()} = {row.shown_canonical()}" for row in rows])
    span = f"rows {rows[0].row} to {rows[-1].row}" if len(rows) > 1 else f"row {rows[0].row}"
    unit = rows[0].unit.written
    found = (
        f"{sample}'s {plural(len(rows), 'result')} ({span}) are reported in {unit}. The other "
        f"{plural(others, matrix + ' result')} are in {majority}. Each was converted exactly: {conversions}."
        + _duplicate_note(site, sample, rows)
    )
    return Draft(
        check="units",
        title=f"{sample} reported in {unit}; the other {matrix} results are in {majority}",
        found=found,
        evidence=tuple(lab_evidence(row) for row in rows),
        rule=RULE,
        source=sources.UNITS,
        scientist_decides=(
            f"The scientist decides whether {unit} is what the lab meant for {sample} or a labelling error to raise "
            "with the lab. The converted values are used everywhere else in this output."
        ),
        samples=(sample,),
    )


def check_units(site: SiteData) -> Outcome:
    drafts: list[Draft] = []
    passed: list[Passed] = []
    for matrix in ("soil", "water"):
        rows = [row for row in site.lab if row.matrix == matrix]
        if not rows:
            continue
        canonical = CANONICAL[matrix]
        counts = Counter(row.unit.key for row in rows)
        majority_key = max(counts, key=lambda key: (counts[key], key == spelling_key(canonical)))
        majority = next(row.unit.written for row in rows if row.unit.key == majority_key)
        majority = canonical if spelling_key(canonical) == majority_key else majority
        odd: dict[str, list[LabRow]] = {}
        for row in rows:
            if row.unit.key != majority_key:
                odd.setdefault(row.sample_id, []).append(row)
        for sample, sample_rows in odd.items():
            drafts.append(_draft(site, sample, sample_rows, majority, counts[majority_key]))
        passed.append(_matrix_pass(matrix, rows, majority_key, majority, counts[majority_key]))
    return Outcome(
        check="units",
        what_was_checked=f"The unit of each of the {len(site.lab)} results, within each matrix.",
        rule=RULE,
        source=sources.UNITS,
        checked=len(site.lab),
        drafts=tuple(drafts),
        passed=tuple(passed),
    )


def _matrix_pass(matrix: Matrix, rows: list[LabRow], key: str, majority: str, count: int) -> Passed:
    spellings: dict[str, list[str]] = {}
    for row in rows:
        if row.unit.key == key:
            samples = spellings.setdefault(row.unit.written, [])
            if row.sample_id not in samples:
                samples.append(row.sample_id)
    base = (
        f"All {plural(count, matrix + ' result')} are in {majority}"
        if count == len(rows)
        else f"{count} of the {len(rows)} {matrix} results are in {majority}"
    )
    if len(spellings) == 1:
        return Passed(check="units", what=f"{base}.")
    variants = join_words(
        [f"{_spelling_note(spelling)} in {join_words(samples)}" for spelling, samples in spellings.items()]
    )
    evidence = tuple(lab_evidence(next(row for row in rows if row.unit.written == spelling)) for spelling in spellings)
    return Passed(
        check="units",
        what=(
            f"{base}, written {len(spellings)} ways: {variants}. These are one unit spelled differently, read as "
            f"{CANONICAL[matrix]}, and not an inconsistency."
        ),
        evidence=evidence,
    )
