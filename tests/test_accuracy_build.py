"""The Accuracy page's data: the verification summary, the held-out search scorer and scripts/build_accuracy.py."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from evidenceline import verification
from evidenceline.guidance import evaluate
from evidenceline.guidance.models import Passage

REPO_ROOT = Path(__file__).resolve().parents[1]
ACCURACY = REPO_ROOT / "web" / "public" / "data" / "accuracy.json"
DASHES = ("\u2013", "\u2014")


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("build_accuracy", REPO_ROOT / "scripts" / "build_accuracy.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in the script look their module up here
    spec.loader.exec_module(module)
    return module


def _passes() -> tuple[dict[str, Any], dict[str, Any]]:
    folder = verification.VERIFICATION_DIR
    return (
        json.loads((folder / "pass-a.json").read_text(encoding="utf-8")),
        json.loads((folder / "pass-b.json").read_text(encoding="utf-8")),
    )


# ---------- verification summary ----------


def test_summary_file_matches_both_passes() -> None:
    written = verification.SUMMARY_PATH.read_text(encoding="utf-8")
    assert written == verification.summary_text(verification.summary_from_files())


def test_every_value_in_the_data_files_was_checked_by_both_passes() -> None:
    summary = verification.summary_from_files()
    ids = [v["id"] for v in summary["values"]]
    assert ids == [v.key for v in verification.file_values()]
    assert len(ids) == 16
    assert all(v["verified_value_matches_file"] for v in summary["values"])


def test_note_only_mismatch_keeps_the_value_confirmed_and_lists_the_note() -> None:
    summary = verification.summary_from_files()
    row = next(v for v in summary["values"] if v["id"] == "water:nemp-3.0:PFOS+PFHxS")
    assert row["verdicts"]["A"]["verdict"] == "confirmed"
    assert row["verdicts"]["B"]["verdict"] == "confirmed"
    note = next(n for n in summary["notes_not_confirmed"] if n["id"] == "water:nemp-3.0:PFOS+PFHxS")
    assert note["verdicts"]["A"]["verdict"] == "mismatch"
    assert note["verdicts"]["B"]["verdict"] == "mismatch"
    assert any("PFOS only, PFHxS only, and the sum of the two" in q for q in note["source_says"]["B"])


def test_a_value_mismatch_stays_a_mismatch() -> None:
    pass_a, pass_b = _passes()
    changed = copy.deepcopy(pass_a)
    record = next(v for v in changed["values"] if v["id"] == "guidelines.json/current/PFOS")
    record["verdict"] = "mismatch"
    record["reason"] = "Wrong page."
    summary = verification.build_summary(changed, pass_b, verification.file_values())
    row = next(v for v in summary["values"] if v["id"] == "water:current:PFOS")
    assert row["verdicts"]["A"]["verdict"] == "mismatch"
    assert row["both_confirm"] is False
    assert summary["counts"]["values_not_confirmed_by_both"] == 1


def test_a_value_changed_after_verification_is_flagged() -> None:
    pass_a, pass_b = _passes()
    values = [v if v.key != "soil:S-Zn-A" else dataclasses.replace(v, value="7500") for v in verification.file_values()]
    summary = verification.build_summary(pass_a, pass_b, values)
    row = next(v for v in summary["values"] if v["id"] == "soil:S-Zn-A")
    assert row["verified_value_matches_file"] is False
    assert summary["counts"]["verified_value_differs_from_file"] == 1


def test_a_value_missing_from_a_pass_is_an_error() -> None:
    pass_a, pass_b = _passes()
    changed = copy.deepcopy(pass_b)
    changed["records"] = [r for r in changed["records"] if r["id"] != "soil:S-Cu-A"]
    with pytest.raises(ValueError, match=r"soil:S-Cu-A .* is not in pass B"):
        verification.build_summary(pass_a, changed, verification.file_values())


# ---------- held-out scorer ----------


def _passage(doc: str, section: str | None, pdf_page: int | None = None) -> Passage:
    return Passage.model_construct(document_id=doc, section=section, pdf_page=pdf_page, rank=1)


def test_heldout_file_loads_24_answerable_and_6_out_of_scope() -> None:
    golden = evaluate.load_questions(evaluate.HELDOUT_PATH)
    assert len(golden.in_scope) == 24
    assert len(golden.out_of_scope) == 6
    assert {q.group for q in golden.in_scope} == {evaluate.HELDOUT_GROUP}


def test_heldout_web_page_entries_count_subsections() -> None:
    golden = evaluate.load_heldout()
    h23 = next(q for q in golden.in_scope if q.id == "h23")
    expected = h23.expected[0]
    assert expected.matches(_passage("adwg-pfas", "Health considerations > GenX Chemicals"))
    assert not expected.matches(_passage("adwg-pfas", "Health considerationsX"))
    assert not expected.matches(_passage("adwg-pfas", "Measurement"))


def test_golden_sections_still_match_exactly() -> None:
    golden = evaluate.load_golden()
    g13 = next(q for q in golden.in_scope if q.id == "g13")
    assert not g13.expected[0].matches(_passage("adwg-pfas", "Treatment of drinking water > Anything"))
    assert g13.expected[0].matches(_passage("adwg-pfas", "Treatment of drinking water"))


def test_heldout_entry_with_nothing_to_match_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"id": "z1", "question": "q", "expected": [{"doc_id": "nemp-3.0"}]}]), "utf-8")
    with pytest.raises(ValueError, match="z1"):
        evaluate.load_questions(bad)


# ---------- build_accuracy.py ----------

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_tidy" name="test_a"/>
  <testcase classname="tests.test_tidy_tools" name="test_b"/>
  <testcase classname="tests.test_api" name="test_c"><failure message="x">boom</failure></testcase>
  <testcase classname="tests.test_api" name="test_d"><error message="y">setup failed</error></testcase>
  <testcase classname="tests.test_guidance_golden" name="test_e"><skipped message="no index"/></testcase>
  <testcase classname="tests.test_new_thing" name="test_f"/>
  <testcase classname="tests.test_new_thing" name="test_g"><skipped type="pytest.xfail" message="known"/></testcase>
</testsuite></testsuites>
"""


def test_parse_junit_counts_by_area_and_names_failures() -> None:
    areas = {a.name: a for a in _script().parse_junit(JUNIT)}
    assert (areas["Tidy lab results"].passed, areas["Tidy lab results"].failed) == (2, 0)
    api = areas["Web service and hosted connector"]
    assert (api.passed, api.failed) == (0, 2)
    assert api.failures == ["test_api::test_c", "test_api::test_d"]
    assert areas["Guidance search"].skipped == 1
    assert areas["test_new_thing"].passed == 1


def test_every_test_file_has_a_plain_english_area() -> None:
    script = _script()
    unnamed = [
        path.stem
        for path in sorted((REPO_ROOT / "tests").glob("test_*.py"))
        if script.area_of(f"tests.{path.stem}") == path.stem
    ]
    assert unnamed == [], "add these test files to AREAS in scripts/build_accuracy.py"


def test_parse_junit_publishes_an_expected_failure_as_a_failure() -> None:
    area = {a.name: a for a in _script().parse_junit(JUNIT)}["test_new_thing"]
    assert (area.passed, area.failed, area.skipped) == (1, 1, 0)
    assert area.failures == ["test_new_thing::test_g (known failure)"]


def test_build_writes_the_shape_the_page_reads() -> None:
    script = _script()
    areas = script.parse_junit(JUNIT)
    out = cast(dict[str, Any], script.build(areas, verification.summary_from_files(), dt.date(2026, 9, 24)))
    assert out["status"] == "published"
    assert out["last_run"] == "2026-09-24"
    assert out["build"] in ("local build",) or out["build"].startswith("commit ")
    assert "not reviewed by a practitioner" in out["method"]
    values = out["verification"]["values"]
    assert len(values) == 16
    assert all(set(v["verdicts"]) == {"A", "B"} for v in values)
    assert [n["id"] for n in out["verification"]["notes"]] == ["water:nemp-3.0:PFOS+PFHxS", "soil:S-As-A"]
    assert out["verification"]["notes"][1]["verdicts"]["A"]["verdict"] == "could not verify"
    assert not any(d in json.dumps(out, ensure_ascii=False) for d in DASHES)


def test_published_accuracy_file_has_no_dashes_and_real_results() -> None:
    text = ACCURACY.read_text(encoding="utf-8")
    assert not any(d in text for d in DASHES)
    data = json.loads(text)
    assert data["status"] == "published"
    assert sum(s["passed"] for s in data["suites"]) > 700
    assert data["verification"]["counts"] == verification.summary_from_files()["counts"]
