from decimal import Decimal
from pathlib import Path

import pytest

from evidenceline.dataset import Dataset, load_dataset
from evidenceline.errors import EvidencelineError


def test_mb2_has_four_rounds_with_pfos_on_row_14_and_pfhxs_on_row_15(data: Dataset) -> None:
    rounds = data.rounds("MB2")
    assert [r.date.isoformat() for r in rounds] == ["2024-03-12", "2024-11-19", "2025-09-16", "2026-05-05"]
    assert [r.lab_report for r in rounds] == ["SYN-240312", "SYN-241119", "SYN-250916", "SYN-260505"]
    assert [r.file for r in rounds] == [f"mb2_round{i}_lab.csv" for i in range(1, 5)]
    for round_ in rounds:
        assert round_.results["PFOS"].row == 14
        assert round_.results["PFHxS"].row == 15


def test_values_are_exact_decimals(data: Dataset) -> None:
    pfos = [r.results["PFOS"].value for r in data.rounds("MB2")]
    pfhxs = [r.results["PFHxS"].value for r in data.rounds("MB2")]
    assert pfos == [Decimal("0.062"), Decimal("0.041"), Decimal("0.038"), Decimal("0.006")]
    assert pfhxs == [Decimal("0.021"), Decimal("0.018"), Decimal("0.019"), Decimal("0.009")]
    assert all(isinstance(v, Decimal) for v in pfos + pfhxs)


def test_row_numbers_match_the_csv_lines(data: Dataset) -> None:
    """Row 14 in the tool output must be line 14 when the file is opened (header is line 1)."""
    from importlib import resources

    text = (resources.files("evidenceline") / "data" / "mb2_round3_lab.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert ",PFOS,0.038," in lines[13]
    assert ",PFHxS,0.019," in lines[14]


def test_non_detects_carry_the_detection_limit(data: Dataset) -> None:
    pfoa = data.rounds("MB2")[0].results["PFOA"]
    assert pfoa.value is None
    assert not pfoa.detected
    assert pfoa.detection_limit == Decimal("0.001")
    assert pfoa.reported == "<0.001"


def test_well_lookup_is_case_insensitive_and_unknown_wells_are_clear_errors(data: Dataset) -> None:
    assert data.resolve_well(" mb2 ") == "MB2"
    with pytest.raises(EvidencelineError, match=r"Unknown well 'MB7'\. Wells with data: MB2\."):
        data.resolve_well("MB7")


def test_rules_are_loaded_with_exact_values(data: Dataset) -> None:
    nemp = data.resolve_rule("nemp-3.0")
    current = data.resolve_rule("CURRENT")
    assert {limit.key: limit.value for limit in nemp.limits} == {"PFOS+PFHxS": Decimal("0.07"), "PFOA": Decimal("0.56")}
    assert {limit.key: limit.value for limit in current.limits} == {
        "PFOS": Decimal("0.008"),
        "PFHxS": Decimal("0.03"),
        "PFOA": Decimal("0.2"),
        "PFBS": Decimal("1"),
    }
    assert (nemp.table, nemp.page, nemp.page_basis) == ("Table 4", "57", "PDF page")
    assert (current.table, current.page, current.page_basis) == ("Table 4", "49", "printed page")
    with pytest.raises(EvidencelineError, match=r"Unknown rule 'nemp-2\.0'"):
        data.resolve_rule("nemp-2.0")


def test_malformed_lab_file_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "bad_lab.csv").write_text(
        "lab_report_id,site_id,well_id,sample_id,sample_date,matrix,analyte,result,unit,lor\n"
        "R1,S,MB1,X,2025-01-01,groundwater,PFOS,lots,ug/L,0.001\n",
        encoding="utf-8",
    )
    (tmp_path / "guidelines.json").write_text('{"rules": []}', encoding="utf-8")
    with pytest.raises(EvidencelineError, match=r"bad_lab\.csv row 2 could not be read"):
        load_dataset(tmp_path)


def test_missing_columns_and_empty_folders_are_clear_errors(tmp_path: Path) -> None:
    with pytest.raises(EvidencelineError, match="No lab files"):
        load_dataset(tmp_path)
    (tmp_path / "x_lab.csv").write_text("analyte,result\nPFOS,1\n", encoding="utf-8")
    with pytest.raises(EvidencelineError, match="missing columns"):
        load_dataset(tmp_path)
