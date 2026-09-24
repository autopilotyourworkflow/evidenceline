"""The notes re-checks in the verification summary, the lookup_limit fix a re-check asked for, held-out set 2 and
the way scripts/build_accuracy.py shows them on the Accuracy page."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import importlib.util
import json
import re
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest

from evidenceline import verification
from evidenceline.core import lookup_limit
from evidenceline.guidance import evaluate
from evidenceline.models import LimitInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "src" / "evidenceline" / "server.py"
ACCURACY_PATH = REPO_ROOT / "web" / "public" / "data" / "accuracy.json"
DASHES = ("\u2013", "\u2014")
NEMP_SUM = "water:nemp-3.0:PFOS+PFHxS"
ARSENIC = "soil:S-As-A"


def _script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "build_accuracy_rechecks", REPO_ROOT / "scripts" / "build_accuracy.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _inputs() -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    folder = verification.VERIFICATION_DIR
    pass_a = json.loads((folder / "pass-a.json").read_text(encoding="utf-8"))
    pass_b = json.loads((folder / "pass-b.json").read_text(encoding="utf-8"))
    return pass_a, pass_b, verification.load_rechecks()


def _note(summary: dict[str, Any], note_id: str) -> dict[str, Any]:
    return next(n for n in summary["notes_not_confirmed"] if n["id"] == note_id)


def _finding(summary: dict[str, Any], finding_id: str) -> dict[str, Any]:
    return next(f for f in summary["recheck_findings"] if f["id"] == finding_id)


# ---------- the re-checks in the summary ----------


def test_both_recheck_files_are_read_in_name_order() -> None:
    rechecks = verification.load_rechecks()
    assert [name for name, _ in rechecks] == ["notes-recheck-a.json", "notes-recheck-b.json"]
    summary = verification.summary_from_files()
    assert [(r["id"], r["file"]) for r in summary["rechecks"]] == [
        ("A", "notes-recheck-a.json"),
        ("B", "notes-recheck-b.json"),
    ]


@pytest.mark.parametrize(
    ("location", "key"),
    [
        ("rules[id=nemp-3.0].limits[key=PFOS+PFHxS].note", NEMP_SUM),
        ("criteria[id=S-As-A].note", ARSENIC),
        ("criteria[id=S-PFOS+PFHxS-A].note", "soil:S-PFOS+PFHxS-A"),
    ],
)
def test_recheck_note_locations_map_to_summary_ids(location: str, key: str) -> None:
    assert verification.note_key(location) == key


def test_an_unplaceable_note_location_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"not a guidelines\.json or soil criteria note"):
        verification.note_key("rules[id=nemp-3.0].value")


def test_both_reworded_notes_are_confirmed_by_every_recheck_on_their_current_wording() -> None:
    summary = verification.summary_from_files()
    for note_id in (NEMP_SUM, ARSENIC):
        note = _note(summary, note_id)
        assert note["changed_since_verification"] is True
        assert set(note["rechecks"]) == {"A", "B"}
        for verdict in note["rechecks"].values():
            assert verdict["verdict"] == "confirmed"
            assert verdict["judged_current_note"] is True
            assert verdict["checked_note"] == note["file_note"]
        assert note["confirmed_by_every_recheck"] is True
    counts = summary["counts"]
    assert (counts["notes_rechecked"], counts["notes_confirmed_by_every_recheck"]) == (2, 2)


def test_the_first_passes_verdicts_stay_about_the_old_wording() -> None:
    note = _note(verification.summary_from_files(), NEMP_SUM)
    assert (note["verdicts"]["A"]["verdict"], note["verdicts"]["B"]["verdict"]) == ("mismatch", "mismatch")
    assert "no separate value" in note["checked_note"]


def test_a_recheck_of_older_wording_does_not_confirm_the_current_note() -> None:
    pass_a, pass_b, rechecks = _inputs()
    values = [
        v if v.key != ARSENIC else dataclasses.replace(v, note=v.note + " Reworded again.")
        for v in verification.file_values()
    ]
    summary = verification.build_summary(pass_a, pass_b, values, rechecks)
    note = _note(summary, ARSENIC)
    assert note["confirmed_by_every_recheck"] is False
    assert all(v["judged_current_note"] is False for v in note["rechecks"].values())
    problems = _script().open_recheck_problems(summary)
    assert any("not confirmed by every re-check" in p for p in problems)


def test_a_note_mismatch_in_a_recheck_is_counted_and_never_changes_the_data() -> None:
    pass_a, pass_b, rechecks = _inputs()
    changed = copy.deepcopy(rechecks)
    record = next(r for r in changed[1][1]["notes"] if r["location"] == "criteria[id=S-As-A].note")
    record["verdict"] = "mismatch"
    record["reason"] = "Invented for this test."
    before = [dataclasses.asdict(v) for v in verification.file_values()]
    summary = verification.build_summary(pass_a, pass_b, verification.file_values(), changed)
    assert [dataclasses.asdict(v) for v in verification.file_values()] == before
    note = _note(summary, ARSENIC)
    assert note["rechecks"]["B"]["verdict"] == "mismatch"
    assert note["confirmed_by_every_recheck"] is False
    assert summary["counts"]["recheck_mismatches"] == 2  # this one and the lookup_limit fields finding
    assert summary["counts"]["recheck_mismatches_open"] == 1
    problems = _script().open_recheck_problems(summary)
    assert any("MISMATCH in the note for Arsenic" in p for p in problems)


def test_an_unresolved_finding_mismatch_is_reported_as_open() -> None:
    pass_a, pass_b, rechecks = _inputs()
    changed = copy.deepcopy(rechecks)
    record = next(r for r in changed[1][1]["server_wording"] if r["id"] == "server/INSTRUCTIONS")
    record["verdict"] = "mismatch"
    summary = verification.build_summary(pass_a, pass_b, verification.file_values(), changed)
    assert summary["counts"]["recheck_mismatches_open"] == 1
    view = _script().verification_view(summary)
    finding = next(f for f in view["recheck_findings"] if f["id"] == "server:instructions")
    assert finding["open"] is True
    assert any("MISMATCH, not resolved" in p for p in _script().open_recheck_problems(summary))


def test_an_unknown_recheck_finding_is_an_error() -> None:
    pass_a, pass_b, rechecks = _inputs()
    changed = copy.deepcopy(rechecks)
    changed[0][1]["server_wording"].append(
        {"id": "something new", "file": "x.py", "verdict": "confirmed", "reason": ""}
    )
    with pytest.raises(ValueError, match="unknown finding 'something new'"):
        verification.build_summary(pass_a, pass_b, verification.file_values(), changed)


def test_rechecks_need_distinct_ids() -> None:
    pass_a, pass_b, rechecks = _inputs()
    with pytest.raises(ValueError, match="distinct ids"):
        verification.build_summary(pass_a, pass_b, verification.file_values(), [rechecks[0], rechecks[0]])


def test_findings_hold_the_related_soil_note_the_server_wording_and_the_lookup_limit_fields() -> None:
    summary = verification.summary_from_files()
    ids = [f["id"] for f in summary["recheck_findings"]]
    assert ids == ["soil:S-PFOS+PFHxS-A", "server:instructions", "server:lookup_limit", "core:lookup_limit-fields"]
    soil = _finding(summary, "soil:S-PFOS+PFHxS-A")
    assert {v["verdict"] for v in soil["verdicts"].values()} == {"could_not_verify"}
    assert soil["first_passes"] is not None
    assert "Both first passes confirmed" in soil["first_passes"]
    fields = _finding(summary, "core:lookup_limit-fields")
    assert fields["verdicts"]["A"]["verdict"] == "mismatch"
    assert fields["verdicts"]["B"]["verdict"] == "not checked"
    assert fields["resolution"] == verification.RESOLUTIONS["core:lookup_limit-fields"]
    assert summary["counts"]["recheck_mismatches"] == 1
    assert summary["counts"]["recheck_mismatches_open"] == 0


def test_the_server_sentences_the_rechecks_judged_are_still_in_server_py() -> None:
    summary = verification.summary_from_files()
    for finding_id in ("server:instructions", "server:lookup_limit"):
        for verdict in _finding(summary, finding_id)["verdicts"].values():
            assert verdict["judged_wording_unchanged"] is True, finding_id


# ---------- the lookup_limit fix re-check A asked for (the claim in RESOLUTIONS) ----------


@pytest.mark.parametrize("analyte", ["PFOS", "PFHxS"])
def test_lookup_limit_names_the_member_alone_as_the_compared_quantity(analyte: str) -> None:
    out = lookup_limit(analyte, "nemp-3.0")
    assert out.compared_quantity == f"{analyte} on its own"
    assert out.applies_to == "sum"  # what the rule sets the value for
    assert out.value == "0.07"


def test_lookup_limit_still_names_the_sum_for_a_sum_lookup_and_one_analyte_under_current() -> None:
    assert lookup_limit("PFOS+PFHxS", "nemp-3.0").compared_quantity == "sum of PFOS and PFHxS"
    assert lookup_limit("PFOS", "current").compared_quantity == "PFOS"
    assert lookup_limit("PFOA", "nemp-3.0").compared_quantity == "PFOA"


def test_no_description_promises_an_either_or_answer_any_more() -> None:
    server = " ".join(SERVER.read_text(encoding="utf-8").split())
    assert "whether it applies to the analyte on its own or to the sum" not in server
    applies_to = LimitInfo.model_fields["applies_to"].description or ""
    compared = LimitInfo.model_fields["compared_quantity"].description or ""
    assert "also applies to PFOS on its own" in applies_to
    assert "on its own" in compared
    assert not any(d in applies_to + compared for d in DASHES)


# ---------- held-out set 2 ----------


def _ids_and_questions(golden: evaluate.Golden) -> tuple[set[str], set[str]]:
    items = [*golden.in_scope, *golden.out_of_scope]
    return {i.id for i in items}, {i.question for i in items}


def test_heldout2_loads_24_answerable_and_6_out_of_scope_in_three_groups() -> None:
    held = evaluate.load_heldout2()
    assert len(held.in_scope) == 24
    assert len(held.out_of_scope) == 6
    groups = [q.group for q in held.in_scope]
    assert (groups.count("casual"), groups.count("practitioner")) == (12, 12)
    assert {q.group for q in held.out_of_scope} == {"out of scope"}
    assert all(e.subsections_count for q in held.in_scope for e in q.expected)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def test_heldout2_shares_no_question_with_the_tuning_sets_except_the_ones_it_says() -> None:
    """Held-out set 2 was written without seeing the other sets, but p07 (landfill disposal limits) came out almost
    word for word like h05 in held-out set 1, which the search was tuned on. The loose check (all words, Jaccard at
    least 0.6) finds only that one; the stricter check below finds the rest. Every pair it finds must be named."""
    held = evaluate.load_heldout2()
    items = [*held.in_scope, *held.out_of_scope]
    near: set[tuple[str, str]] = set()
    for other in (evaluate.load_golden(), evaluate.load_heldout()):
        others = [*other.in_scope, *other.out_of_scope]
        assert not {i.question for i in items} & {o.question for o in others}
        for h in items:
            for o in others:
                a, b = _words(h.question), _words(o.question)
                if len(a & b) / len(a | b) >= 0.6:
                    near.add((h.id, o.id))
    assert near <= set(_script().HELDOUT2_NEAR_COPIES)
    assert ("p07", "h05") in near
    assert len(items) == 30


_STOP_LINES = (
    "a an the of to in on for and or is are be it its this that what which how do does did i we you my our your",
    "with at by from as if can any there their they them than then so not no should must would could when where who",
    "whom have has had was were will just about into out up some such per each other only also more most very much",
    "many",
)
CONTENT_STOP_WORDS = frozenset(word for line in _STOP_LINES for word in line.split())


def _content_words(text: str) -> set[str]:
    """Words that carry the question: lower case, three letters or more, common words removed (the same rule as
    case A3 in web/tests/adversarial_launch.mjs)."""
    return {w for w in re.sub(r"[^a-z0-9+ ]", " ", text.lower()).split() if len(w) > 2 and w not in CONTENT_STOP_WORDS}


def _places(item: evaluate.GoldenQuestion | evaluate.OutOfScopeQuestion) -> set[tuple[str, str]]:
    if isinstance(item, evaluate.OutOfScopeQuestion):
        return set()
    return set[tuple[str, str]]().union(*(e.targets() for e in item.expected))


def test_every_near_copy_found_by_the_stricter_check_is_named() -> None:
    """Content words only, as exact fractions: Jaccard at least 2/5; or at least 1/4 when both expect the same page
    or section; or at least 3/10 when both are out-of-scope questions. Every pair found must be in
    HELDOUT2_NEAR_COPIES, and every named pair must still be found, so the list cannot drift either way."""
    held = evaluate.load_heldout2()
    tuning = [
        item
        for other in (evaluate.load_golden(), evaluate.load_heldout())
        for item in (*other.in_scope, *other.out_of_scope)
    ]
    near: set[tuple[str, str]] = set()
    for h in (*held.in_scope, *held.out_of_scope):
        for t in tuning:
            a, b = _content_words(h.question), _content_words(t.question)
            share = Fraction(len(a & b), len(a | b))
            same_place = bool(_places(h) & _places(t))
            both_out = isinstance(h, evaluate.OutOfScopeQuestion) and isinstance(t, evaluate.OutOfScopeQuestion)
            if (
                share >= Fraction(2, 5)
                or (share >= Fraction(1, 4) and same_place)
                or (share >= Fraction(3, 10) and both_out)
            ):
                near.add((h.id, t.id))
    assert near == set(_script().HELDOUT2_NEAR_COPIES)


def test_the_page_names_the_near_copy_in_the_fair_sets_note() -> None:
    script = _script()
    note = next(s[2] for s in script.SEARCH_SETS if s[0] == script.FAIR_SET)
    for held, tuned in script.HELDOUT2_NEAR_COPIES:
        assert f"{held} ~ {tuned}" in note
    assert "Seven of its questions" in note
    assert len(script.HELDOUT2_NEAR_COPIES) == 7


def _result(qid: str, group: str, first_rank: int | None = None, correct: bool | None = None) -> Any:
    in_scope = correct is None
    recall8 = (Decimal(1) if first_rank else Decimal(0)) if in_scope else None
    return evaluate.QuestionResult(qid, group, qid, in_scope, "passages found", first_rank, recall8, correct)


def test_the_score_without_the_near_copies_leaves_out_exactly_the_named_questions() -> None:
    script = _script()
    questions = [
        _result("p07", "practitioner", first_rank=1),  # a near copy: left out
        _result("p01", "practitioner", first_rank=1),
        _result("c02", "casual"),  # a near copy that missed: left out too
        _result("c01", "casual", first_rank=3),
        _result("c03", "casual"),
        _result("x06", "out of scope", correct=True),  # a near copy: left out
        _result("x04", "out of scope", correct=False),
    ]
    out = script.without_near_copies(questions)
    assert out["left_out"] == ["c02", "p07", "x06"]
    assert out["questions"] == 4
    metrics = {m["name"]: m for m in out["metrics"]}
    assert (metrics["hit@1"]["hits"], metrics["hit@1"]["of"]) == (1, 3)
    assert (metrics["hit@5"]["hits"], metrics["hit@8"]["hits"]) == (2, 2)
    assert metrics["recall@8"]["value"] == "0.67"
    assert (metrics["out-of-scope accuracy"]["hits"], metrics["out-of-scope accuracy"]["of"]) == (0, 1)
    assert script.without_near_copies([_result("p01", "practitioner", first_rank=1)]) is None
    report = evaluate.EvalReport(questions=questions)
    assert script.near_copy_sentence(report) == (
        "Leaving those seven out, the right page comes first for 1 of 3 questions (0 of 2 casual ones), and 0 of 1 "
        'out-of-scope questions get "not covered".'
    )


def test_the_accuracy_data_shows_the_score_without_the_near_copies() -> None:
    sets = cast(list[dict[str, Any]], json.loads(ACCURACY_PATH.read_text(encoding="utf-8"))["search"])
    fair, casual = sets[0], sets[1]
    script = _script()
    assert fair["near_copies"] == [{"held_out": h, "tuning": t} for h, t in script.HELDOUT2_NEAR_COPIES]
    assert fair["without_near_copies"]["left_out"] == sorted(h for h, _ in script.HELDOUT2_NEAR_COPIES)
    assert fair["without_near_copies"]["questions"] == fair["questions"] - 7
    assert casual["without_near_copies"]["left_out"] == ["c02", "c09"]
    hit1 = next(m for m in fair["without_near_copies"]["metrics"] if m["name"] == "hit@1")
    assert f"comes first for {hit1['hits']} of {hit1['of']} questions" in fair["note"]


def test_heldout2_refuses_an_unknown_prefix_and_a_misplaced_out_of_scope_question(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"id": "q01", "question": "q", "expected": "not_covered"}]), "utf-8")
    with pytest.raises(ValueError, match="q01"):
        evaluate.load_heldout2(bad)
    bad.write_text(json.dumps([{"id": "c01", "question": "q", "expected": "not_covered"}]), "utf-8")
    with pytest.raises(ValueError, match="only 'x' questions"):
        evaluate.load_heldout2(bad)


def test_heldout2_refuses_the_golden_format() -> None:
    with pytest.raises(ValueError, match="not in the held-out format"):
        evaluate.load_heldout2(evaluate.GOLDEN_PATH)


def test_heldout2_flag_scores_exactly_the_heldout2_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[evaluate.Golden] = []

    def fake(golden: evaluate.Golden, *_: object) -> evaluate.EvalReport:
        seen.append(golden)
        return evaluate.EvalReport()

    monkeypatch.setattr(evaluate, "evaluate", fake)
    assert evaluate.main(["--heldout2"]) == 0
    assert _ids_and_questions(seen[0])[0] == _ids_and_questions(evaluate.load_heldout2())[0]
    assert evaluate.main(["--heldout2", "--heldout"]) == 2


# ---------- build_accuracy.py ----------


def test_the_fair_set_comes_first_and_the_tuning_sets_say_they_were_tuned_on() -> None:
    script = _script()
    ids = [s[0] for s in script.SEARCH_SETS]
    assert ids == ["held-out-2", "golden", "held-out"]
    labels = {s[0]: (s[1], s[2]) for s in script.SEARCH_SETS}
    assert "fair" in labels["held-out-2"][0]
    assert "never used for tuning" in labels["held-out-2"][1]
    assert "Tuning set" in labels["golden"][0]
    assert "Tuning set" in labels["held-out"][0]
    assert "tuned on it" in labels["held-out"][1]


def test_a_subset_is_scored_from_the_same_run_and_lists_no_misses_twice() -> None:
    script = _script()
    report = evaluate.EvalReport(in_scope=24, out_of_scope=6)
    report.groups["casual"] = evaluate.Tally(
        in_scope=12, hit1=4, hit5=5, hit8=6, recall8=Decimal("3.5"), out_of_scope=0, oos_correct=0
    )
    out = cast(dict[str, Any], script.subset_json(script.SUBSETS[0], report))
    assert (out["set"], out["part_of"], out["questions"], out["misses"]) == ("held-out-2-casual", "held-out-2", 12, [])
    metrics = {m["name"]: m for m in out["metrics"]}
    assert (metrics["hit@1"]["hits"], metrics["hit@1"]["of"]) == (4, 12)
    assert metrics["recall@8"]["value"] == "0.29"
    assert "out-of-scope accuracy" not in metrics
    with pytest.raises(ValueError, match="no questions in group"):
        script.subset_json(dataclasses.replace(script.SUBSETS[0], group="nothing"), report)


def test_build_shows_the_rechecks_and_their_findings() -> None:
    script = _script()
    out = cast(dict[str, Any], script.build([], verification.summary_from_files(), dt.date(2026, 9, 24)))
    view = out["verification"]
    assert [r["id"] for r in view["rechecks"]] == ["A", "B"]
    assert all(r["method"] and "did not read" in r["method"] for r in view["rechecks"])
    for note in view["notes"]:
        assert note["confirmed_now"] is True
        assert note["status"] == script.NOTE_RECHECKED
        assert {v["verdict"] for v in note["rechecks"].values()} == {"confirmed"}
    findings = {f["id"]: f for f in view["recheck_findings"]}
    assert findings["core:lookup_limit-fields"]["open"] is False
    assert findings["core:lookup_limit-fields"]["resolution"].startswith("Fixed after the re-check")
    assert findings["soil:S-PFOS+PFHxS-A"]["open"] is True  # NEMP 3.1 could not be fetched by either re-check
    assert findings["soil:S-PFOS+PFHxS-A"]["verdicts"]["A"]["verdict"] == "could not verify"
    assert findings["server:instructions"]["open"] is False
    assert not any(d in json.dumps(out, ensure_ascii=False) for d in DASHES)


def test_published_accuracy_file_has_held_out_set_2_first_and_the_rechecks() -> None:
    data = json.loads((REPO_ROOT / "web" / "public" / "data" / "accuracy.json").read_text(encoding="utf-8"))
    if data["search"]:
        assert [s["set"] for s in data["search"]] == ["held-out-2", "held-out-2-casual", "golden", "held-out"]
        assert data["search"][1]["part_of"] == "held-out-2"
    assert [r["id"] for r in data["verification"]["rechecks"]] == ["A", "B"]
    assert data["verification"]["counts"]["recheck_mismatches_open"] == 0
