"""get_results, lookup_limit and compare_rules as plain Python functions."""

from __future__ import annotations

import pytest

from evidenceline import compare_rules, get_results, lookup_limit
from evidenceline.dataset import Dataset
from evidenceline.errors import EvidencelineError
from evidenceline.models import RoundComparison, RuleResult

# --- get_results ------------------------------------------------------------------------------------------------


def test_get_results_returns_every_row_with_provenance() -> None:
    out = get_results("MB2")
    assert out.well == "MB2"
    assert out.site == "FDS-01"
    assert out.matrix == "groundwater"
    assert out.unit == "ug/L"
    assert out.dates == ["2024-03-12", "2024-11-19", "2025-09-16", "2026-05-05"]
    assert out.count == len(out.results) == 4 * 14
    pfos_sep = next(r for r in out.results if r.analyte == "PFOS" and r.date == "2025-09-16")
    assert pfos_sep.model_dump() == {
        "date": "2025-09-16",
        "analyte": "PFOS",
        "result": "0.038",
        "value": "0.038",
        "detected": True,
        "detection_limit": "0.001",
        "unit": "ug/L",
        "lab_report": "SYN-250916",
        "file": "mb2_round3_lab.csv",
        "row": 14,
    }


def test_get_results_filters_by_analyte_case_insensitively() -> None:
    out = get_results("mb2", "pfhxs")
    assert [(r.date, r.value, r.row) for r in out.results] == [
        ("2024-03-12", "0.021", 15),
        ("2024-11-19", "0.018", 15),
        ("2025-09-16", "0.019", 15),
        ("2026-05-05", "0.009", 15),
    ]


def test_get_results_reports_non_detects_without_inventing_a_value() -> None:
    out = get_results("MB2", "PFOA")
    assert all(r.value is None and not r.detected and r.result == "<0.001" for r in out.results)


@pytest.mark.parametrize(
    ("well", "analyte", "message"),
    [
        ("MB3", None, "Unknown well 'MB3'"),
        ("", None, "Unknown well ''"),
        ("MB2", "benzene", "Unknown analyte 'benzene'"),
        ("MB2", "PFOS+PFHxS", "ask for 'PFOS' and 'PFHxS' separately"),
    ],
)
def test_get_results_bad_input(well: str, analyte: str | None, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        get_results(well, analyte)


# --- lookup_limit -----------------------------------------------------------------------------------------------


def test_lookup_limit_current_pfos() -> None:
    out = lookup_limit("PFOS", "current")
    assert (out.value, out.unit, out.applies_to, out.compared_quantity) == ("0.008", "ug/L", "single", "PFOS")
    assert (out.table, out.page, out.page_basis) == ("Table 4", "49", "printed page")
    assert "Version 3.1" in out.source_document
    assert "8 ng/L" in out.note
    assert "A result equal to the limit is not above it." in out.caveats


def test_lookup_limit_nemp_pfos_returns_the_sum_value_and_says_so() -> None:
    out = lookup_limit("PFOS", "nemp-3.0")
    # The rule sets the value for the sum; footnote a applies it to PFOS on its own too, and that is what is compared.
    assert (out.value, out.applies_to, out.compared_quantity) == ("0.07", "sum", "PFOS on its own")
    assert (out.table, out.page, out.page_basis) == ("Table 4", "57", "PDF page")
    assert "Version 3.0" in out.source_document
    assert "adopted for implementation in Western Australia" in out.wa_status
    assert out.caveats[0].startswith("Under this rule the value for the sum of PFOS and PFHxS also applies to PFOS")
    assert '"PFOS only, PFHxS only, and the sum of the two"' in out.caveats[0]
    assert "no separate value" not in out.model_dump_json()
    assert "no value for PFOS" not in out.model_dump_json()


@pytest.mark.parametrize("alias", ["PFOS+PFHxS", "PFOS + PFHxS", "sum", "sum of PFOS and PFHxS"])
def test_lookup_limit_accepts_sum_aliases(alias: str) -> None:
    assert lookup_limit(alias, "nemp-3.0").value == "0.07"


@pytest.mark.parametrize(
    ("analyte", "rule", "value"),
    [("PFHxS", "current", "0.03"), ("PFOA", "current", "0.2"), ("PFBS", "current", "1"), ("PFOA", "nemp-3.0", "0.56")],
)
def test_lookup_limit_values(analyte: str, rule: str, value: str) -> None:
    assert lookup_limit(analyte, rule).value == value


@pytest.mark.parametrize(
    ("analyte", "rule", "message"),
    [
        ("PFOS+PFHxS", "current", "screens PFOS and PFHxS separately"),
        ("PFBS", "nemp-3.0", "has no drinking-water value for PFBS"),
        ("PFOS", "adwg-2011", "Unknown rule 'adwg-2011'. Use one of: nemp-3.0, current"),
        ("lead", "current", "Unknown analyte 'lead'"),
    ],
)
def test_lookup_limit_bad_input(analyte: str, rule: str, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        lookup_limit(analyte, rule)


# --- compare_rules ----------------------------------------------------------------------------------------------


def _rule(out: RoundComparison, rule_id: str) -> RuleResult:
    return next(r for r in out.rules if r.rule == rule_id)


def test_compare_rules_september_2025_rules_disagree_and_the_sum_arithmetic_is_shown() -> None:
    out = compare_rules("MB2", "2025-09-16")
    assert (out.date, out.lab_report, out.file) == ("2025-09-16", "SYN-250916", "mb2_round3_lab.csv")
    nemp = _rule(out, "nemp-3.0")
    current = _rule(out, "current")
    assert nemp.overall == "not above"
    assert current.overall == "above"
    assert out.rules_agree is False
    sum_screen = next(s for s in nemp.screens if s.compared_quantity == "sum of PFOS and PFHxS")
    assert sum_screen.arithmetic == "0.038 + 0.019 = 0.057"
    assert (sum_screen.compared_value, sum_screen.limit, sum_screen.status) == ("0.057", "0.07", "not above")
    assert [s.rows for s in sum_screen.sources if s.kind == "lab result"] == [[14], [15]]
    pfos = next(s for s in current.screens if s.compared_quantity == "PFOS")
    assert (pfos.compared_value, pfos.limit, pfos.status) == ("0.038", "0.008", "above")
    pfhxs = next(s for s in current.screens if s.compared_quantity == "PFHxS")
    assert pfhxs.status == "not above"
    assert "The rules give different answers" in out.notes[0]
    assert any("scientist's call" in note for note in out.notes)


def test_compare_rules_never_picks_a_rule() -> None:
    out = compare_rules("MB2", "Sep 2025")
    assert [r.rule for r in out.rules] == ["nemp-3.0", "current"]
    text = out.model_dump_json().lower()
    for phrase in ("recommended rule", "use the current rule", "use nemp", "preferred rule", "should use"):
        assert phrase not in text


@pytest.mark.parametrize(
    ("date", "nemp", "current"),
    [
        ("12 March 2024", "above", "above"),  # sum 0.083 > 0.07; PFOS 0.062 > 0.008
        ("Nov 2024", "not above", "above"),  # sum 0.059
        ("Sept 2025", "not above", "above"),  # sum 0.057
        ("5 May 2026", "not above", "not above"),  # PFOS 0.006, PFHxS 0.009
    ],
)
def test_compare_rules_every_round(date: str, nemp: str, current: str) -> None:
    out = compare_rules("MB2", date)
    assert (_rule(out, "nemp-3.0").overall, _rule(out, "current").overall) == (nemp, current)
    assert out.rules_agree is (nemp == current)


def test_compare_rules_march_2024_sum() -> None:
    nemp = _rule(compare_rules("MB2", "2024-03-12"), "nemp-3.0")
    assert nemp.screens[0].arithmetic == "0.062 + 0.021 = 0.083"
    assert nemp.screens[0].status == "above"


def test_compare_rules_non_detects_are_screened_by_detection_limit() -> None:
    current = _rule(compare_rules("MB2", "2025-09-16"), "current")
    pfoa = next(s for s in current.screens if s.compared_quantity == "PFOA")
    assert pfoa.status == "not above"
    assert pfoa.compared_value is None
    assert "not detected" in pfoa.explanation


def test_equal_to_the_limit_is_not_above(boundary_data: Dataset) -> None:
    out = compare_rules("MB9", "2025-01-10", data=boundary_data)
    nemp = _rule(out, "nemp-3.0")
    current = _rule(out, "current")
    sum_screen = nemp.screens[0]
    assert sum_screen.arithmetic == "0.008 + 0.062 = 0.07"
    assert sum_screen.status == "not above"
    assert "equals 0.07 ug/L, so it is not above it" in sum_screen.explanation
    pfos = next(s for s in current.screens if s.compared_quantity == "PFOS")
    assert (pfos.compared_value, pfos.status) == ("0.008", "not above")
    pfhxs = next(s for s in current.screens if s.compared_quantity == "PFHxS")
    assert pfhxs.status == "above"  # 0.062 > 0.03


def test_sum_with_a_non_detect_is_bounded(boundary_data: Dataset) -> None:
    nemp = _rule(compare_rules("MB9", "2025-06-10", data=boundary_data), "nemp-3.0")
    screen = nemp.screens[0]
    assert screen.arithmetic == "0.008 + <0.001"
    assert screen.status == "not above"  # even at the detection limit: 0.009 <= 0.07


@pytest.mark.parametrize(
    ("well", "date", "message"),
    [
        ("MB2", "2025-10-01", "No MB2 round on '2025-10-01'"),
        ("MB2", "October 2025", "No MB2 round"),
        ("MB2", "yesterday", "Could not read the date 'yesterday'"),
        ("MB2", "16/09/2025", "Could not read the date"),
        ("MB2", "2025", "Could not read the date"),
        ("MB4", "2025-09-16", "Unknown well 'MB4'"),
    ],
)
def test_compare_rules_bad_input(well: str, date: str, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        compare_rules(well, date)
