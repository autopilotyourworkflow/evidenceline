"""Tidy lab results on the synthetic site FDS-01: six issues flagged exactly once, every trap left unflagged, and
every number matching both the independent script (scripts/expected_tidy.py) and hand-checked arithmetic."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest

from evidenceline.errors import EvidencelineError
from evidenceline.tidy.arith import multiple
from evidenceline.tidy.checks.duplicates import duplicate_pairs, pair_results
from evidenceline.tidy.checks.holding import RULES
from evidenceline.tidy.config import DEFAULT_CONFIG, QaConfig
from evidenceline.tidy.engine import get_review_item, tidy_lab_files
from evidenceline.tidy.models import ReviewItem, TidyResult
from evidenceline.tidy.site import SiteData, packaged_site

REPO = Path(__file__).resolve().parents[1]
SITE_DIR = REPO / "src" / "evidenceline" / "data" / "fds01_site"
CHECK_ORDER = ["sample ids", "units", "field duplicates", "holding times", "blanks", "LOR against criteria"]


def _load_expected() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("expected_tidy", REPO / "scripts" / "expected_tidy.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(dict[str, Any], module.compute())


@pytest.fixture(scope="module")
def result() -> TidyResult:
    return tidy_lab_files("FDS-01")


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return _load_expected()


@pytest.fixture(scope="module")
def site() -> SiteData:
    return packaged_site("FDS-01")


def _item(result: TidyResult, check: str) -> ReviewItem:
    found = [item for item in result.review_items if item.check == check]
    assert len(found) == 1, f"{check}: {[i.title for i in found]}"
    return found[0]


def _one_decimal(value: Fraction) -> Decimal:
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


# The six issues, each exactly once --------------------------------------------------------------------------------


def test_six_review_items_one_per_check(result: TidyResult) -> None:
    assert [item.check for item in result.review_items] == CHECK_ORDER
    assert [item.number for item in result.review_items] == [1, 2, 3, 4, 5, 6]
    assert [item.samples for item in result.review_items] == [
        ["SB3-15", "SB3-1.5"],
        ["MB2"],
        ["SB4-0.3", "QC1"],
        ["SB3-15"],
        ["RB1", "SB2-0.2"],
        ["MB3"],
    ]
    for run in result.checks:
        assert run.review_items == [CHECK_ORDER.index(run.check) + 1]


def test_id_mismatch_is_suggested_not_merged(result: TidyResult) -> None:
    item = _item(result, "sample ids")
    assert "SB3-15" in item.title
    assert "SB3-1.5" in item.title
    assert "12 September 2025 11:40" in item.found
    assert {(e.file, e.row) for e in item.evidence} >= {("chain_of_custody.csv", 7), ("field_sheet.csv", 12)}
    assert [e.row for e in item.evidence if e.file == "lab_results.csv"] == list(range(20, 28))
    sb3 = [row for row in result.rows if row.sample_id == "SB3-15"]
    assert len(sb3) == 8
    assert all(row.coc_row is None and row.field_row is None for row in sb3)
    assert all(row.id_status == "not matched: probably SB3-1.5, not confirmed (review item 1)" for row in sb3)
    assert item.related_items == [4]


def test_units_item_converts_exactly(result: TidyResult) -> None:
    item = _item(result, "units")
    assert [e.row for e in item.evidence] == [52, 53, 54, 55]
    assert "PFOS 38 ng/L = 0.038 ug/L" in item.found
    assert "PFHxS 19 ng/L = 0.019 ug/L" in item.found
    assert "PFOA <1 ng/L = <0.001 ug/L" in item.found
    assert "false RPD of 199.6% instead of 8.2%" in item.found
    mb2 = {row.analyte: row for row in result.rows if row.sample_id == "MB2"}
    assert (mb2["PFOS"].reported, mb2["PFOS"].value, mb2["PFOS"].unit) == ("38 ng/L", "0.038", "ug/L")
    assert (mb2["PFOA"].value, mb2["PFOA"].lor, mb2["PFOA"].detected) == (None, "0.001", False)


def test_duplicate_item_flags_lead_only(result: TidyResult) -> None:
    item = _item(result, "field duplicates")
    assert item.title == "Field duplicate pair SB4-0.3 and QC1: Lead RPD 61.8%, above 50%"
    assert "|180 - 95| / ((180 + 95) / 2) x 100 = 85 / 137.5 x 100 = 61.8%" in item.found
    assert "10 x 5 = 50 mg/kg" in item.found
    assert {e.analyte for e in item.evidence if e.file == "lab_results.csv"} == {"Lead"}
    assert [e.row for e in item.evidence if e.file == "lab_results.csv"] == [32, 40]


def test_holding_item_is_mercury_in_sb3_15(result: TidyResult) -> None:
    item = _item(result, "holding times")
    assert item.title == "Mercury in SB3-15 extracted after the holding time (30 days; limit 28 days)"
    assert [(e.row, e.analyte) for e in item.evidence] == [(25, "Mercury")]
    assert "30 days later" in item.found
    assert "2 days over" in item.found
    assert "not yet matched" in item.found


def test_blank_item_links_auger_a_and_marks_sb2_marginal(result: TidyResult) -> None:
    item = _item(result, "blanks")
    assert item.title == "PFOS detected in rinsate blank RB1; linked to a marginal result at SB2-0.2"
    assert "PFOS 0.004 ug/L, 4 x its LOR of 0.001 ug/L" in item.found
    assert "Hand auger A on the same day for SB1-0.2, SB1-1.0, SB2-0.2, SB4-0.3, SB4-1.0 and QC1" in item.found
    assert "0.0022 + 0.0012 = 0.0034 mg/kg" in item.found
    assert "1.13 x the value: marginal" in item.found
    assert "165.67 x the value, above it by more than 1.5 x" in item.found
    assert "SB4-1.0 has no lab results (held at the lab)" in item.found
    assert "SB4-0.3 and QC1 were not analysed for PFOS" in item.found
    lab_rows = {e.row for e in item.evidence if e.file == "lab_results.csv"}
    assert lab_rows == {44, 9, 10}
    field_rows = {e.sample_id for e in item.evidence if e.file == "field_sheet.csv"}
    assert "SB3-0.5" not in field_rows
    assert "MB1" not in field_rows


def test_lor_item_shows_both_rules_without_choosing(result: TidyResult) -> None:
    item = _item(result, "LOR against criteria")
    assert item.title.startswith("MB3 PFOS: LOR above drinking-water value (0.008 ug/L) under the current national")
    assert "the LOR (0.01 ug/L) is above the value" in item.found
    assert "between 0 and 0.02 ug/L" in item.found
    assert "PFAS NEMP 3.0" in item.found
    assert "not above the value even with non-detects at their LOR" in item.found
    assert "Which applies is the scientist's call" in item.scientist_decides
    assert [e.row for e in item.evidence] == [56, 57]


def test_every_item_has_rule_source_and_scientist_decides(result: TidyResult) -> None:
    for item in result.review_items:
        assert item.rule
        assert item.source
        assert item.evidence
        assert item.scientist_decides.startswith("The scientist decides")
        assert '"' in item.source  # sources are quoted verbatim


# Traps: checked, and not flagged -----------------------------------------------------------------------------------


def test_traps_are_not_flagged(result: TidyResult) -> None:
    flagged = {sample for item in result.review_items for sample in item.samples}
    for trap in ("SB4-1.0", "FB1", "CB1", "TB1", "MB1", "SB3-0.5", "SB1-1.0", "QC2"):
        assert trap not in flagged
    flagged_rows = {(e.file, e.row) for item in result.review_items for e in item.evidence}
    assert ("lab_results.csv", 17) not in flagged_rows  # SB3-0.5 mercury, extracted on day 28
    assert ("lab_results.csv", 33) not in flagged_rows  # SB4-0.3 mercury, RPD 66.7% below 10 x LOR


def test_checked_and_not_flagged_lists_the_traps(result: TidyResult) -> None:
    text = " ".join(p.what for p in result.checked_not_flagged)
    assert "SB4-1.0 is on the chain of custody marked HOLD" in text
    assert "held at the lab, not missing" in text
    assert "µg/L (micro sign) in MB1" in text
    assert "μg/L (Greek mu) in MB3" in text
    assert "'12/09/25 09:10'" in text
    assert "No date was read month first" in text
    assert "Mercury 66.7%" in text
    assert "Cadmium 40.0% (QC1 <0.4 mg/kg, its LOR used)" in text
    assert "Zinc 15.7%" in text
    assert "PFOS 8.2% and PFHxS 5.4%" in text
    assert "SB3-0.5 Mercury at 28 days, at the limit and not over it" in text
    assert "SB3-15 Arsenic at 30 days" in text
    for blank in ("FB1", "CB1", "TB1"):
        assert f"{blank} (" in text
    assert "between 0.0015 and 0.0017 mg/kg" in text
    assert "PFHxS <0.01 μg/L (<0.01 ug/L) against the drinking-water value for PFHxS (0.03 ug/L)" in text
    not_checked = " ".join(n.what + " " + n.reason for n in result.not_checked)
    assert "PFHxA" in not_checked
    assert "No investigation level for PFHxA" in not_checked


def test_never_says_no_issues_and_keeps_private_header_out(result: TidyResult) -> None:
    dumped = result.model_dump_json()
    assert "no issues" not in dumped.lower()
    assert "Harbourline" not in dumped
    assert "Example Road" not in dumped
    assert "Welshpool" not in dumped
    assert any("client and a site address" in note for note in result.notes)
    assert any("investigation level, not a finding" in note for note in result.notes)
    assert "Synthetic data" in result.synthetic


def test_no_em_or_en_dashes_anywhere(result: TidyResult) -> None:
    texts = [result.model_dump_json()]
    texts += [get_review_item("FDS-01", n).model_dump_json() for n in range(1, 7)]
    texts += [path.read_text(encoding="utf-8") for path in SITE_DIR.iterdir() if path.is_file()]
    for text in texts:
        assert chr(0x2014) not in text  # em dash
        assert chr(0x2013) not in text  # en dash


# Agreement with the independent script ----------------------------------------------------------------------------


def test_counts_match_script(result: TidyResult, expected: dict[str, Any]) -> None:
    assert (result.results, result.samples) == (expected["results"], expected["samples"])
    by_matrix = {m: sum(c.results for c in result.counts if c.matrix == m) for m in ("soil", "water")}
    assert by_matrix == {"soil": expected["soil"], "water": expected["water"]} == {"soil": 41, "water": 32}
    assert len(result.rows) == 73
    assert [(f.file, f.header_row, f.data_rows) for f in result.files] == [
        ("lab_results.csv", 2, 73),
        ("chain_of_custody.csv", 2, 16),
        ("field_sheet.csv", 7, 16),
    ]


def test_ids_and_units_match_script(result: TidyResult, expected: dict[str, Any]) -> None:
    assert expected["lab_only"] == ["SB3-15"]
    assert expected["coc_without_results_not_held"] == ["SB3-1.5"]
    assert expected["held"] == ["SB4-1.0"]
    assert expected["unit_flags"] == {"MB2": [e.analyte for e in _item(result, "units").evidence]}
    mb2 = {row.analyte: row for row in result.rows if row.sample_id == "MB2"}
    for analyte, (value, lor) in cast(dict[str, tuple[Fraction | None, Fraction]], expected["mb2_ug_per_l"]).items():
        assert Decimal(mb2[analyte].lor) == Decimal(lor.numerator) / Decimal(lor.denominator)
        if value is None:
            assert mb2[analyte].value is None
        else:
            assert Decimal(cast(str, mb2[analyte].value)) == Decimal(value.numerator) / Decimal(value.denominator)


def test_rpds_match_script_exactly(site: SiteData, expected: dict[str, Any]) -> None:
    script = cast(dict[tuple[str, str], tuple[Fraction | None, str]], expected["rpd"])
    seen = 0
    for dup in duplicate_pairs(site):
        results = pair_results(site, dup, DEFAULT_CONFIG)
        assert results is not None
        for pair in results:
            value, verdict = script[(dup.parent_sample, pair.analyte)]
            assert pair.verdict == verdict
            assert pair.rpd == (None if value is None else _one_decimal(value))
            seen += 1
    assert seen == len(script) == 12


def test_rpd_arithmetic_hand_checked(site: SiteData) -> None:
    rpds = {
        (dup.parent_sample, pair.analyte): pair
        for dup in duplicate_pairs(site)
        for pair in pair_results(site, dup, DEFAULT_CONFIG) or []
    }
    # Lead: |180 - 95| / 137.5 x 100 = 680/11 = 61.818...
    assert Fraction(85, 1) / Fraction(275, 2) * 100 == Fraction(680, 11)
    assert rpds[("SB4-0.3", "Lead")].rpd == Decimal("61.8")
    assert rpds[("SB4-0.3", "Lead")].mean == Decimal("137.5")
    # Cadmium: QC1 <0.4 takes 0.4; |0.6 - 0.4| / 0.5 x 100 = 40 exactly
    assert rpds[("SB4-0.3", "Cadmium")].rpd == Decimal("40.0")
    assert rpds[("SB4-0.3", "Mercury")].rpd == Decimal("66.7")  # 0.1 / 0.15 x 100
    assert rpds[("SB4-0.3", "Zinc")].rpd == Decimal("15.7")  # 45 / 287.5 x 100 = 15.65...
    # MB2 converted: 0.003 / 0.0365 x 100 = 600/73 = 8.219...; PFHxS 0.001 / 0.0185 x 100 = 200/37 = 5.405...
    assert rpds[("MB2", "PFOS")].rpd == _one_decimal(Fraction(600, 73)) == Decimal("8.2")
    assert rpds[("MB2", "PFHxS")].rpd == _one_decimal(Fraction(200, 37)) == Decimal("5.4")
    assert rpds[("MB2", "PFOA")].rpd is None


def test_naive_rpd_matches_script(expected: dict[str, Any], result: TidyResult) -> None:
    naive = cast(Fraction, expected["naive_mb2_pfos_rpd"])
    assert naive == Fraction(37965, 1000) / Fraction(38035, 2000) * 100
    assert f"false RPD of {_one_decimal(naive)}%" in _item(result, "units").found


def test_holding_days_match_script(result: TidyResult, expected: dict[str, Any]) -> None:
    days = cast(dict[tuple[str, str], int], expected["holding_days"])
    for row in result.rows:
        assert row.days_to_extraction == days[(row.sample_id, row.analyte)]
    assert expected["holding_breaches"] == [("SB3-15", "Mercury", 30, 28)]
    # Hand check: 12 September to 12 October 2025 is 18 days left in September plus 12 in October.
    assert (dt.date(2025, 10, 12) - dt.date(2025, 9, 12)).days == 18 + 12 == 30
    mercury = next(rule for rule in RULES if rule.name == "Mercury in soil")
    assert mercury.deadline(dt.date(2025, 9, 12)) == dt.date(2025, 10, 10)
    metals = next(rule for rule in RULES if rule.name == "Other metals in soil")
    assert metals.deadline(dt.date(2025, 9, 12)) == dt.date(2026, 3, 12)
    assert metals.deadline(dt.date(2025, 8, 31)) == dt.date(2026, 2, 28)


def test_blanks_and_lor_match_script(result: TidyResult, expected: dict[str, Any]) -> None:
    assert expected["blank_detections"] == [("RB1", "PFOS", Fraction(4, 1000), Fraction(4))]
    assert expected["blank_linked"] == {"RB1": ["SB1-0.2", "SB1-1.0", "SB2-0.2", "SB4-0.3", "SB4-1.0", "QC1"]}
    assert expected["marginal"] == [("RB1", "SB2-0.2", Fraction(34, 10000), Fraction(17, 15))]
    assert multiple(Decimal("0.0034"), Decimal("0.003")) == Decimal("1.13")  # 17/15 = 1.1333...
    assert expected["lor_not_confirmed"] == [("MB3", "PFOS", "current")]
    assert _item(result, "LOR against criteria").samples == ["MB3"]


# get_review_item ---------------------------------------------------------------------------------------------------


def test_review_item_detail_quotes_the_files_exactly(result: TidyResult) -> None:
    lines = {
        name: (SITE_DIR / name).read_text(encoding="utf-8").splitlines()
        for name in ("lab_results.csv", "chain_of_custody.csv", "field_sheet.csv")
    }
    for number in range(1, 7):
        detail = get_review_item("FDS-01", number)
        assert detail.item == result.review_items[number - 1]
        assert detail.of_total == 6
        for line in detail.source_lines:
            assert line.text == lines[line.file][line.row - 1]
    detail = get_review_item("fds-01", 3)
    assert [line.row for line in detail.source_lines] == [15, 32, 40]
    assert detail.source_lines[1].text.startswith("SYN-250912,SYN-250912_006,SB4-0.3,")


@pytest.mark.parametrize("number", [0, 7, -1])
def test_review_item_out_of_range(number: int) -> None:
    with pytest.raises(EvidencelineError, match=r"Items are numbered 1 to 6"):
        get_review_item("FDS-01", number)


def test_unknown_site_is_a_clear_error() -> None:
    with pytest.raises(EvidencelineError, match=r"Unknown site 'FDS-99'.*FDS-01.*not available yet"):
        tidy_lab_files("FDS-99")
    with pytest.raises(EvidencelineError, match=r"Unknown site"):
        get_review_item("C:/data/site", 1)


# Settings ----------------------------------------------------------------------------------------------------------


def test_thresholds_are_configurable(site: SiteData) -> None:
    strict = tidy_lab_files("FDS-01", data=site, config=QaConfig(rpd_review_above=Decimal(10)))
    item = _item(strict, "field duplicates")
    assert item.title == "Field duplicate pair SB4-0.3 and QC1: Lead RPD 61.8% and Zinc RPD 15.7%, above 10%"
    assert "15.7% is above 10% (review)" in item.found
    assert strict.settings.rpd_review_above_percent == "10"
    loose = tidy_lab_files(
        "FDS-01", data=site, config=QaConfig(rpd_review_above=Decimal(70), rpd_investigate_above=Decimal(80))
    )
    assert "field duplicates" not in [i.check for i in loose.review_items]
    wide = tidy_lab_files("FDS-01", data=site, config=QaConfig(blank_marginal_factor=Decimal(200)))
    assert _item(wide, "blanks").samples == ["RB1", "SB1-0.2", "SB2-0.2"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rpd_review_above": Decimal(0)}, "must be positive"),
        ({"rpd_review_above": Decimal(60)}, "cannot be below"),
        ({"blank_marginal_factor": Decimal(1)}, "must be above 1"),
    ],
)
def test_bad_settings_are_rejected(kwargs: dict[str, Decimal], message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        QaConfig(**kwargs)


def test_output_is_json_serialisable_with_string_numbers(result: TidyResult) -> None:
    payload = cast(dict[str, Any], json.loads(result.model_dump_json()))
    rows = cast(list[dict[str, Any]], payload["rows"])
    assert all(isinstance(row["lor"], str) and "E" not in row["lor"] for row in rows)
    assert all(row["value"] is None or (isinstance(row["value"], str) and "E" not in row["value"]) for row in rows)
