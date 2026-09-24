"""Tidy lab results: exact units, day-first dates, CSV reading, and clear errors for bad input files."""

from __future__ import annotations

import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from evidenceline.errors import EvidencelineError
from evidenceline.tidy.dates import parse_clock, parse_stamp
from evidenceline.tidy.engine import tidy_lab_files
from evidenceline.tidy.files import read_table
from evidenceline.tidy.site import load_site_folder
from evidenceline.tidy.units import parse_matrix, parse_unit

SITE_DIR = Path(__file__).resolve().parents[1] / "src" / "evidenceline" / "data" / "fds01_site"


# Units ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["ug/L", "ug/l", "UG/L", "µg/L", "μg/L", "mcg/L", " µg / L "])
def test_microgram_spellings_are_one_unit(written: str) -> None:
    unit = parse_unit(written)
    assert (unit.key, unit.matrix, unit.exponent, unit.canonical) == ("ug/l", "water", 0, "ug/L")


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("38", "ng/L", "0.038"),
        ("1", "ng/L", "0.001"),
        ("0.0001", "mg/L", "0.1"),
        ("0.035", "ug/L", "0.035"),
        ("0.41", "mg/kg", "0.41"),
        ("180", "ug/kg", "0.18"),
    ],
)
def test_conversion_is_exact(value: str, unit: str, expected: str) -> None:
    converted = parse_unit(unit).to_canonical(Decimal(value))
    assert converted == Decimal(expected)
    assert isinstance(converted, Decimal)


def test_unknown_unit_and_matrix_are_clear_errors() -> None:
    with pytest.raises(EvidencelineError, match=r"Unknown unit 'ppb'\. Units understood: ng/L, ug/L"):
        parse_unit("ppb")
    assert parse_matrix("S") == parse_matrix("Soil") == "soil"
    assert parse_matrix("W") == parse_matrix("groundwater") == "water"
    with pytest.raises(EvidencelineError, match=r"Unknown matrix 'air'"):
        parse_matrix("air")


# Dates ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "date", "time"),
    [
        ("12/09/25", dt.date(2025, 9, 12), None),
        ("12/09/25 11:40", dt.date(2025, 9, 12), dt.time(11, 40)),
        ("12/09/2025", dt.date(2025, 9, 12), None),
        ("12 Sep 2025 09:10 AM", dt.date(2025, 9, 12), dt.time(9, 10)),
        ("12 Sep 2025 01:40 PM", dt.date(2025, 9, 12), dt.time(13, 40)),
        ("12 Sep 2025 12:05 AM", dt.date(2025, 9, 12), dt.time(0, 5)),
        ("12 September 2025", dt.date(2025, 9, 12), None),
        ("2025-09-12", dt.date(2025, 9, 12), None),
        ("1/2/26", dt.date(2026, 2, 1), None),
    ],
)
def test_dates_are_read_day_first(text: str, date: dt.date, time: dt.time | None) -> None:
    stamp = parse_stamp(text)
    assert (stamp.date, stamp.time, stamp.written) == (date, time, text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("09/13/25", r"there is no month 13\. The month is the second number"),
        ("31/02/2025", r"day is out of range"),
        ("12 Smarch 2025", r"'Smarch' is not a month name"),
        ("yesterday", r"Could not read the date 'yesterday'\. Dates are read day first"),
        ("12 Sep 2025 13:40 PM", r"hour 13 does not go with AM or PM"),
        ("12/09/25 25:00", r"is not a time of day"),
        ("", r"A date is missing"),
    ],
)
def test_bad_dates_are_clear_errors(text: str, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        parse_stamp(text)


def test_clock() -> None:
    assert parse_clock("09:10") == dt.time(9, 10)
    assert parse_clock("") is None
    with pytest.raises(EvidencelineError, match=r"Use hh:mm"):
        parse_clock("9am")


def test_stamps_agree_on_date_and_minute() -> None:
    assert parse_stamp("12/09/25 11:40").agrees_with(parse_stamp("12 Sep 2025 11:40 AM"))
    assert parse_stamp("12/09/25").agrees_with(parse_stamp("12 Sep 2025 11:40 AM"))
    assert not parse_stamp("12/09/25 11:40").agrees_with(parse_stamp("12 Sep 2025 11:41 AM"))
    assert not parse_stamp("12/09/25").agrees_with(parse_stamp("09/12/25"))


# CSV reading ------------------------------------------------------------------------------------------------------


def test_preamble_and_row_numbers() -> None:
    text = '# SYNTHETIC\n# Client: Somebody (fictional)\n\na,b\n1,2\n\n3,"x, y"\n'
    table = read_table("t.csv", text, ["a", "b"])
    assert table.header_row == 4
    assert [(r.row, r.get("a"), r.get("b")) for r in table.records] == [(5, "1", "2"), (7, "3", "x, y")]
    assert table.preamble == {"client": "Somebody (fictional)"}
    assert table.line(7) == '3,"x, y"'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# only a preamble\n", r"t\.csv is empty"),
        ("a,c\n1,2\n", r"t\.csv is missing columns: b\. The header \(row 1\) has: a, c"),
        ("a,b,a\n1,2,3\n", r"repeated columns: a"),
        ("a,b\n1,2\n3\n", r"t\.csv row 3 has 1 cells; the header has 2"),
        ("a,b\n", r"has a header but no data rows"),
    ],
)
def test_malformed_csv_is_a_clear_error(text: str, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        read_table("t.csv", text, ["a", "b"])


# Bad input files --------------------------------------------------------------------------------------------------


@pytest.fixture
def site_copy(tmp_path: Path) -> Path:
    folder = tmp_path / "site"
    shutil.copytree(SITE_DIR, folder)
    return folder


def _edit(folder: Path, name: str, old: str, new: str, *, every: bool = False) -> None:
    path = folder / name
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, -1 if every else 1), encoding="utf-8")


def test_copy_of_the_site_gives_the_same_result(site_copy: Path) -> None:
    copied = tidy_lab_files("FDS-01", data=load_site_folder(site_copy))
    assert copied == tidy_lab_files("FDS-01")


@pytest.mark.parametrize(
    ("name", "old", "new", "message"),
    [
        (
            "lab_results.csv",
            ",38,ng/L,1,ng/L,",
            ",38,ppt,1,ppt,",
            r"lab_results\.csv row 52 could not be read: Unknown unit 'ppt'",
        ),
        (
            "lab_results.csv",
            ",0.41,mg/kg,",
            ",n/a,mg/kg,",
            r"row 3 could not be read: The result 'n/a' is not a plain number",
        ),
        (
            "lab_results.csv",
            "<,0.0002,mg/kg,0.0002",
            "<,0.0005,mg/kg,0.0002",
            r"row 6 could not be read: a '<' result must equal the LOR",
        ),
        (
            "lab_results.csv",
            "PFOS,1763-23-1,,0.41,mg/kg,0.0002,mg/kg",
            "PFOS,1763-23-1,,0.41,ug/L,0.0002,ug/L",
            r"row 3 could not be read: a soil result cannot be in ug/L",
        ),
        ("lab_results.csv", "PFOS,1763-23-1,,0.41", "PFOS,1763-23-1,>,0.41", r"prefix '>' is not understood"),
        ("lab_results.csv", "12 Sep 2025 09:10 AM", "09/13/25 09:10", r"row 3 could not be read: .*no month 13"),
        (
            "lab_results.csv",
            "SB1-1.0,12 Sep 2025 09:25 AM,Soil,PFOS",
            "SB1-0.2,12 Sep 2025 09:25 AM,Soil,PFOS",
            r"two results for PFOS in SB1-0\.2 \(rows 3 and 6\)",
        ),
        (
            "chain_of_custody.csv",
            "12/09/25 09:10,S",
            "13/13/25 09:10,S",
            r"chain_of_custody\.csv row 3 could not be read: .*no month 13",
        ),
        ("chain_of_custody.csv", "PFAS-3,N,", "PFAS-3,maybe,", r"row 3 could not be read: hold must be Y or N"),
        (
            "chain_of_custody.csv",
            "COC-FDS-01,SB1-1.0,",
            "COC-FDS-01,SB1-0.2,",
            r"lists SB1-0\.2 twice \(rows 3 and 4\)",
        ),
        (
            "field_sheet.csv",
            "field_duplicate,SB4-0.3",
            "field_duplicate,SB9-9.9",
            r"duplicate QC1 names parent sample SB9-9\.9",
        ),
        (
            "field_sheet.csv",
            "Hand auger A,primary",
            "Hand auger A,main",
            r"row 8 could not be read: qa_type 'main' is not one of",
        ),
        (
            "field_sheet.csv",
            "SB1-0.2,12/09/2025,09:10",
            "SB1-0.2,12/09/2025,9am",
            r"field_sheet\.csv row 8 could not be read: .*Use hh:mm",
        ),
        ("site.json", '"site_id": "FDS-01",', "", r"site\.json could not be read"),
        ("soil_criteria.json", '"value": "0.003"', '"value": "three"', r"soil_criteria\.json could not be read"),
    ],
)
def test_bad_input_files_are_clear_errors(site_copy: Path, name: str, old: str, new: str, message: str) -> None:
    _edit(site_copy, name, old, new)
    with pytest.raises(EvidencelineError, match=message):
        load_site_folder(site_copy)


def test_missing_file_is_a_clear_error(site_copy: Path) -> None:
    (site_copy / "chain_of_custody.csv").unlink()
    with pytest.raises(EvidencelineError, match=r"The site folder has no chain_of_custody\.csv"):
        load_site_folder(site_copy)


def test_sampling_time_disagreement_becomes_a_review_item(site_copy: Path) -> None:
    _edit(site_copy, "chain_of_custody.csv", "MB1,16/09/25 09:30", "MB1,16/09/25 10:30")
    result = tidy_lab_files("FDS-01", data=load_site_folder(site_copy))
    ids = [item for item in result.review_items if item.check == "sample ids"]
    assert [item.title for item in ids] == [
        "Sample id written two ways: SB3-15 in the lab file, SB3-1.5 on the chain of custody",
        "Sampling date or time for MB1 differs between files",
    ]
    assert "chain of custody 16 September 2025 10:30" in ids[1].found
    assert "lab file 16 September 2025 09:30" in ids[1].found
    assert len(result.review_items) == 7


def test_unmatched_ids_without_a_near_match(site_copy: Path) -> None:
    _edit(site_copy, "lab_results.csv", "SB3-15,12 Sep 2025 11:40 AM", "SB3-15,12 Sep 2025 11:50 AM", every=True)
    result = tidy_lab_files("FDS-01", data=load_site_folder(site_copy))
    titles = [item.title for item in result.review_items if item.check == "sample ids"]
    assert titles == [
        "SB3-15 is in the lab file but not in the chain of custody",
        "SB3-1.5 is in the chain of custody (not marked HOLD) but not in the lab file",
    ]


def test_no_detections_in_blanks_means_no_blank_item(site_copy: Path) -> None:
    _edit(site_copy, "lab_results.csv", "PFOS,1763-23-1,,0.004,ug/L", "PFOS,1763-23-1,<,0.001,ug/L")
    result = tidy_lab_files("FDS-01", data=load_site_folder(site_copy))
    assert "blanks" not in [item.check for item in result.review_items]
    assert any(p.what.startswith("RB1 (rinsate blank)") for p in result.checked_not_flagged)
