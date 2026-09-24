"""Build the Accuracy page's data file, ``web/public/data/accuracy.json``, from real runs.

Usage::

    .venv/Scripts/python scripts/build_accuracy.py                        run pytest, both search sets, and write
    .venv/Scripts/python scripts/build_accuracy.py --junit report.xml     use an existing pytest JUnit report
    .venv/Scripts/python scripts/build_accuracy.py --summary              only rewrite verification/summary.json
    .venv/Scripts/python scripts/build_accuracy.py --check-summary        exit 1 if summary.json is out of date

What goes in:

1. **Tests.** The whole pytest suite, counted by area (one area per group of test files), with every failure named.
2. **Search.** ``evidenceline.guidance.evaluate`` on held-out set 2 (written without seeing the search and never
   used for tuning: the fair score), on its casual questions alone, and on the two tuning sets: the golden set and
   held-out set 1 (written without seeing the search, first scored at 6 of 24, then used for tuning). Every miss is
   listed. Needs the local guidance index; without it the search section is left out and the script says so.
3. **Guideline verification.** ``src/evidenceline/data/verification/summary.json``, rebuilt here from the two
   independent passes and the later notes re-checks (``evidenceline.verification``): each value with both passes'
   verdicts, every explanatory note that a pass did not confirm with the re-checks' verdicts on its current wording,
   and the re-checks' other findings. A re-check mismatch that is not resolved is printed as a warning; the data is
   never changed here.

The build is "local build", or "commit <sha>" when ``GITHUB_SHA`` is set. The script writes the file even when a
test fails (failures are shown on the page), and then exits 1. Rates are exact fractions; nothing is a float.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from evidenceline import verification
from evidenceline.guidance import evaluate
from evidenceline.guidance.search import default_index_path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "web" / "public" / "data" / "accuracy.json"
DASHES = re.compile("[\u2013\u2014]")

Json = dict[str, Any]

METHOD = (
    "Guideline values are checked by two independent automated passes against the source pages, not reviewed by a "
    "practitioner."
)

AREAS: tuple[tuple[str, str], ...] = (
    # (test file name prefix, area shown on the page); the first match wins.
    ("test_tidy", "Tidy lab results"),
    ("test_checker", "Paragraph checker"),
    ("test_textparse", "Paragraph checker"),
    ("test_drafting", "Fill in the numbers"),
    ("test_tools", "Results, limits and both rules"),
    ("test_dataset", "Results, limits and both rules"),
    ("test_units", "Results, limits and both rules"),
    ("test_guidance", "Guidance search"),
    ("test_answer", "Question box answers"),
    ("test_api", "Web service and hosted connector"),
    ("test_server_redaction", "Redaction"),
    ("test_redact", "Redaction"),
    ("test_server", "MCP server over stdio"),
    ("test_adversarial", "Independent adversarial tests"),
    ("test_export_web_data", "Website data and packaging"),
    ("test_packaging", "Website data and packaging"),
    ("test_accuracy_build", "Website data and packaging"),
    ("test_accuracy_rechecks", "Website data and packaging"),
    ("test_phase2_fixes", "Fixes to issues the testers found"),
)

PASS_METHODS = {
    "A": "read NEMP 3.0 and ASC NEPM Schedule B1 page by page from the official PDFs, and NEMP 3.1 through a text "
    "rendering of the official PDF. It did not read pass B.",
    "B": "downloaded every source itself, NEMP 3.1 included, and read each table's row, column and footnotes. It did "
    "not read pass A.",
}

RECHECK_METHODS = {
    "A": "downloaded PFAS NEMP 3.0 (the archived copy of the official file) and Schedule B1 again, read the table "
    "pages as text and as images, and ran lookup_limit. It did not read re-check B or the first two passes.",
    "B": "downloaded PFAS NEMP 3.0 (the archived copy of the official file) and Schedule B1 again, and compared each "
    "note word by word with the table and its footnotes. It did not read re-check A or the first two passes.",
}

NOTE_NOT_CONFIRMED = "At least one pass did not confirm this note."
NOTE_CHANGED = (
    "The note was reworded after both passes checked it, to follow the source text they quoted. Their verdicts are "
    "about the earlier wording shown here; the new wording has not been re-checked by either pass."
)
NOTE_RECHECKED = (
    "The note was reworded after both passes checked it, to follow the source text they quoted. Their verdicts are "
    "about the earlier wording shown here. Every later re-check read the source again and confirmed the wording the "
    "file holds now."
)
NOTE_RECHECK_OPEN = (
    "The note was reworded after both passes checked it. Not every later re-check confirmed the wording the file "
    "holds now; their verdicts are shown."
)
WORDING_CHANGED = "The wording has changed since this re-check, so its verdict is about earlier wording."

FAIR_SET = "held-out-2"
"""The one set the search was never tuned on. Its score is the fair one; the page leads with it."""

HELDOUT2_NEAR_COPIES = (
    ("c02", "h21"),
    ("c09", "g13"),
    ("p06", "h01"),
    ("p07", "h05"),
    ("x03", "x03"),
    ("x05", "o04"),
    ("x06", "x06"),
)
"""Held-out set 2 questions close in wording to a tuning question, as (held-out id, tuning id); a test finds every
such pair. They were written without seeing the other sets, but seven came out close to one (p07 to h05 in held-out
set 1 almost word for word; the rest share the main words, and the in-scope ones the expected page too). They are
kept and scored as written, and the page names them and also gives the score without them."""

SEARCH_SETS = (
    (
        FAIR_SET,
        "Held-out set (the fair score)",
        "Written from the documents alone, without seeing the search, and never used for tuning. Seven of its "
        "questions turned out close in wording to a question in a tuning set (held-out id ~ tuning id): "
        "c02 ~ h21, c09 ~ g13, p06 ~ h01, p07 ~ h05, x03 ~ x03, x05 ~ o04 and x06 ~ x06. They are kept and scored "
        "as written.",
        evaluate.HELDOUT2_PATH,
        evaluate.load_heldout2,
    ),
    (
        "golden",
        "Tuning set 1 (golden questions)",
        "Used to tune the search, so these scores flatter it.",
        evaluate.GOLDEN_PATH,
        evaluate.load_golden,
    ),
    (
        "held-out",
        "Tuning set 2 (first held-out set)",
        "Written without seeing the search. Its first run put the right page first for 6 of 24 questions; the search "
        "was then tuned on it, so these scores flatter it too.",
        evaluate.HELDOUT_PATH,
        evaluate.load_heldout,
    ),
)


@dataclass(frozen=True, slots=True)
class Subset:
    """One group of a question set, shown as its own card from the same run."""

    part_of: str
    group: str
    set_id: str
    label: str
    note: str


SUBSETS = (
    Subset(
        FAIR_SET,
        "casual",
        "held-out-2-casual",
        "Held-out set, casual questions only",
        "The questions typed the way a non-expert would ask. Their misses are listed with the whole held-out set. "
        "Two of them, c02 and c09, are among the seven close to a tuning question.",
    ),
)


@dataclass
class Area:
    name: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list[str])

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "failures": self.failures,
        }


def area_of(classname: str) -> str:
    """The page's area for a JUnit classname such as 'tests.test_api' or 'tests.test_api.TestAsk'."""
    parts = classname.split(".")
    module = next((p for p in parts if p.startswith("test_")), parts[-1])
    return next((name for prefix, name in AREAS if module.startswith(prefix)), module)


XFAIL_TYPE = "pytest.xfail"
"""How pytest's JUnit report marks a test that is expected to fail."""


def parse_junit(xml_text: str) -> list[Area]:
    """Test counts by area from a pytest JUnit XML report. Errors count as failures, and so does a test marked
    as an expected failure (pytest writes it as skipped with type "pytest.xfail"): it records a known product
    failure, so it is published as one, never as skipped."""
    root = ET.fromstring(xml_text)
    areas: dict[str, Area] = {}
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        area = areas.setdefault(area_of(classname), Area(area_of(classname)))
        module = classname.split(".")[-1]
        skipped = case.find("skipped")
        if case.find("failure") is not None or case.find("error") is not None:
            area.failed += 1
            area.failures.append(f"{module}::{case.get('name', '')}")
        elif skipped is not None and skipped.get("type") == XFAIL_TYPE:
            area.failed += 1
            area.failures.append(f"{module}::{case.get('name', '')} (known failure)")
        elif skipped is not None:
            area.skipped += 1
        else:
            area.passed += 1
    order = [name for _, name in AREAS]
    return sorted(areas.values(), key=lambda a: (order.index(a.name) if a.name in order else len(order), a.name))


def run_pytest() -> str:
    """Run the whole suite once and return its JUnit XML report."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"],
            cwd=REPO_ROOT,
            check=False,
        )
        if not report.exists():
            raise SystemExit("build_accuracy: pytest did not write a JUnit report; see its output above.")
        return report.read_text(encoding="utf-8")


def _count(hits: int, of: int) -> dict[str, Any]:
    return {"hits": hits, "of": of}


def _metrics(tally: evaluate.Tally) -> list[Json]:
    metrics: list[Json] = []
    if tally.in_scope:
        recall = (tally.recall8 / tally.in_scope).quantize(Decimal("0.01"))
        metrics += [
            {"name": "hit@1", **_count(tally.hit1, tally.in_scope)},
            {"name": "hit@5", **_count(tally.hit5, tally.in_scope)},
            {"name": "hit@8", **_count(tally.hit8, tally.in_scope)},
            {"name": "recall@8", "value": str(recall)},
        ]
    if tally.out_of_scope:
        metrics.append({"name": "out-of-scope accuracy", **_count(tally.oos_correct, tally.out_of_scope)})
    return metrics


def _whole(report: evaluate.EvalReport) -> evaluate.Tally:
    return evaluate.Tally(
        report.in_scope, report.hit1, report.hit5, report.hit8, report.recall8, report.out_of_scope, report.oos_correct
    )


def _tally(questions: Iterable[evaluate.QuestionResult]) -> evaluate.Tally:
    """Counts for some of a run's questions, worked out from each question's own result (no new search)."""
    tally = evaluate.Tally()
    for q in questions:
        if q.in_scope:
            tally.in_scope += 1
            tally.hit1 += q.first_rank == 1
            tally.hit5 += q.first_rank is not None and q.first_rank <= evaluate.TOP_K
            tally.hit8 += q.first_rank is not None
            tally.recall8 += q.recall8 if q.recall8 is not None else Decimal(0)
        else:
            tally.out_of_scope += 1
            tally.oos_correct += q.correct is True
    return tally


def _hits(tally: evaluate.Tally) -> str:
    return f"{tally.hit1} of {tally.in_scope}"


def without_near_copies(questions: Sequence[evaluate.QuestionResult]) -> Json | None:
    """The same run's score with held-out set 2's near copies of tuning questions left out, or None when none of
    ``questions`` is one."""
    left_out = {held for held, _ in HELDOUT2_NEAR_COPIES}
    dropped = sorted(q.id for q in questions if q.id in left_out)
    if not dropped:
        return None
    tally = _tally(q for q in questions if q.id not in left_out)
    return {"left_out": dropped, "questions": tally.in_scope + tally.out_of_scope, "metrics": _metrics(tally)}


def near_copy_sentence(report: evaluate.EvalReport) -> str:
    """One sentence for the fair set's note: the first-place score with the near copies left out."""
    kept = [q for q in report.questions if q.id not in {held for held, _ in HELDOUT2_NEAR_COPIES}]
    whole, casual = _tally(kept), _tally(q for q in kept if q.group == "casual")
    return (
        f"Leaving those seven out, the right page comes first for {_hits(whole)} questions ({_hits(casual)} casual "
        f'ones), and {whole.oos_correct} of {whole.out_of_scope} out-of-scope questions get "not covered".'
    )


def _set_json(set_id: str, label: str, note: str, report: evaluate.EvalReport) -> Json:
    misses: list[dict[str, str]] = []
    for q in report.questions:
        if q.in_scope and q.first_rank is None:
            result = 'said "not covered"' if q.status == "not covered" else "the right page was not in the top eight"
            misses.append({"id": q.id, "question": q.question, "result": result})
        elif not q.in_scope and not q.correct:
            misses.append({"id": q.id, "question": q.question, "result": 'gave passages instead of "not covered"'})
    out: Json = {
        "set": set_id,
        "label": label,
        "note": note,
        "questions": report.in_scope + report.out_of_scope,
        "metrics": _metrics(_whole(report)),
        "misses": misses,
    }
    if set_id == FAIR_SET:
        out["note"] = f"{note} {near_copy_sentence(report)}"
        out["near_copies"] = [{"held_out": held, "tuning": tuned} for held, tuned in HELDOUT2_NEAR_COPIES]
        out["without_near_copies"] = without_near_copies(report.questions)
    return out


def search_set(set_id: str, label: str, note: str, path: Path, load: Callable[[Path], evaluate.Golden]) -> Json:
    """Score one question set. Each set has its own loader, so a held-out file is refused if it is ever in the
    golden (tuning) format, instead of being shown as "never tuned on"."""
    return _set_json(set_id, label, note, evaluate.evaluate(load(path)))


def subset_json(subset: Subset, report: evaluate.EvalReport) -> Json:
    """One group of a scored set, from the same run (the search is not run again). Its misses stay with the whole
    set, so nothing is listed twice."""
    tally = report.groups.get(subset.group)
    if tally is None:
        raise ValueError(f"{subset.part_of} has no questions in group {subset.group!r}")
    out: Json = {
        "set": subset.set_id,
        "part_of": subset.part_of,
        "label": subset.label,
        "note": subset.note,
        "questions": tally.in_scope + tally.out_of_scope,
        "metrics": _metrics(tally),
        "misses": [],
    }
    if subset.part_of == FAIR_SET:
        out["without_near_copies"] = without_near_copies([q for q in report.questions if q.group == subset.group])
    return out


def search_sets() -> list[Json]:
    """Every set in ``SEARCH_SETS`` in order, each followed by its ``SUBSETS``; each set is searched once."""
    out: list[Json] = []
    for set_id, label, note, path, load in SEARCH_SETS:
        report = evaluate.evaluate(load(path))
        out.append(_set_json(set_id, label, note, report))
        out.extend(subset_json(subset, report) for subset in SUBSETS if subset.part_of == set_id)
    return out


def _plain(verdict: str) -> str:
    """Verdict codes in words: 'could_not_verify' becomes 'could not verify'."""
    return verdict.replace("_", " ")


def _first_sentence(text: str) -> str:
    head, dot, _ = text.partition(". ")
    return head + ("." if dot else "")


def verification_view(summary: dict[str, Any]) -> dict[str, Any]:
    """The part of the summary the page shows, in the shape ``web/src/lib/accuracy.ts`` reads."""
    passes = [
        {"id": str(p["id"]), "label": "Independent automated pass", "method": PASS_METHODS[str(p["id"])]}
        for p in cast(list[dict[str, Any]], summary["passes"])
    ]
    values = [
        {
            "id": v["id"],
            "label": v["label"],
            "value": v["value"],
            "unit": v["unit"],
            "source": v["source"],
            "verdicts": {
                pid: {"verdict": _plain(str(d["verdict"])), "note": _first_sentence(str(d["note"]))}
                for pid, d in cast(dict[str, dict[str, Any]], v["verdicts"]).items()
            },
            "note": "" if v["verified_value_matches_file"] else "The data file changed after this value was verified.",
        }
        for v in cast(list[dict[str, Any]], summary["values"])
    ]
    notes = [
        {
            "id": n["id"],
            "label": n["label"],
            "file_note": n["file_note"],
            "checked_note": n["checked_note"] if n["changed_since_verification"] else None,
            "status": _note_status(n),
            "confirmed_now": bool(n["confirmed_by_every_recheck"]),
            "verdicts": {
                pid: {"verdict": _plain(str(d["verdict"])), "note": str(d["note"])}
                for pid, d in cast(dict[str, dict[str, Any]], n["verdicts"]).items()
            },
            "rechecks": {
                rid: {"verdict": _plain(str(d["verdict"])), "note": _recheck_note(d)}
                for rid, d in cast(dict[str, Json], n["rechecks"]).items()
            },
            "source_says": next(
                (
                    q
                    for pid in ("B", "A")
                    for q in cast(dict[str, list[str]], n["source_says"])[pid]
                    if "\ufffd" not in q
                ),
                "",
            ),
        }
        for n in cast(list[dict[str, Any]], summary["notes_not_confirmed"])
    ]
    rechecks = [
        {
            "id": str(r["id"]),
            "label": "Independent automated re-check of the reworded notes",
            "method": RECHECK_METHODS.get(str(r["id"]), str(r["method"])),
            "date": str(r["date"]),
        }
        for r in cast(list[Json], summary["rechecks"])
    ]
    findings = [
        {
            "id": f["id"],
            "label": f["subject"],
            "context": f["first_passes"] or "",
            "verdicts": {
                rid: {"verdict": _plain(str(d["verdict"])), "note": _recheck_note(d)}
                for rid, d in cast(dict[str, Json], f["verdicts"]).items()
            },
            "resolution": f["resolution"] or "",
            "open": _finding_open(f),
        }
        for f in cast(list[Json], summary["recheck_findings"])
    ]
    return {
        "passes": passes,
        "rechecks": rechecks,
        "values": values,
        "notes": notes,
        "recheck_findings": findings,
        "counts": summary["counts"],
    }


def _note_status(note: Json) -> str:
    if not note["changed_since_verification"]:
        return NOTE_NOT_CONFIRMED
    if not note["rechecks"]:
        return NOTE_CHANGED
    return NOTE_RECHECKED if note["confirmed_by_every_recheck"] else NOTE_RECHECK_OPEN


def _stale(verdict: Json) -> bool:
    """True when the words a re-check judged are no longer the ones in the file."""
    return verdict.get("judged_current_note") is False or verdict.get("judged_wording_unchanged") is False


def _recheck_note(verdict: Json) -> str:
    """A re-check's reason, plus a warning when the words it judged are no longer the ones in the file. A re-check
    that did not look at the item has no words to go stale."""
    reason = str(verdict["note"])
    if verdict["verdict"] == "not checked" or not _stale(verdict):
        return reason
    return f"{reason} {WORDING_CHANGED}".strip()


def _finding_open(finding: Json) -> bool:
    """A finding needs a person when it is not resolved in code and a re-check did not confirm the current words."""
    if finding["resolution"] is not None:
        return False
    verdicts = cast(dict[str, Json], finding["verdicts"]).values()
    checked = [d for d in verdicts if d["verdict"] != "not checked"]
    return any(d["verdict"] != verification.CONFIRMED or _stale(d) for d in checked)


def open_recheck_problems(summary: Json) -> list[str]:
    """Re-check results that need a person: a mismatch not resolved, or a reworded note not confirmed by every
    re-check. Printed as warnings; the data is never changed here."""
    problems: list[str] = []
    for n in cast(list[Json], summary["notes_not_confirmed"]):
        for rid, d in cast(dict[str, Json], n["rechecks"]).items():
            if d["verdict"] == "mismatch":
                problems.append(f"re-check {rid} found a MISMATCH in the note for {n['label']}: {d['note']}")
        if n["changed_since_verification"] and n["rechecks"] and not n["confirmed_by_every_recheck"]:
            problems.append(f"the reworded note for {n['label']} is not confirmed by every re-check")
    for f in cast(list[Json], summary["recheck_findings"]):
        for rid, d in cast(dict[str, Json], f["verdicts"]).items():
            if d["verdict"] == "mismatch" and f["resolution"] is None:
                problems.append(f"re-check {rid} found a MISMATCH, not resolved: {f['subject']}: {d['note']}")
            elif _stale(d):
                problems.append(f"re-check {rid} judged earlier wording of: {f['subject']}")
    return problems


def build(areas: list[Area] | None, summary: dict[str, Any], today: dt.date) -> dict[str, Any]:
    sha = os.environ.get("GITHUB_SHA", "").strip()
    search: list[dict[str, Any]] = []
    if default_index_path().exists():
        search = search_sets()
    else:
        print("build_accuracy: no guidance index, so the search section is left out (run scripts/build_index.py)")
    out: dict[str, Any] = {
        "_about": "Generated by scripts/build_accuracy.py; do not edit by hand.",
        "status": "published",
        "message": "Results from the automated test run.",
        "method": METHOD,
        "last_run": today.isoformat(),
        "build": f"commit {sha[:7]}" if sha else "local build",
        "commit": sha[:7] or None,
        "suites": [a.as_json() for a in areas or []],
        "search": search,
        "verification": verification_view(summary),
    }
    text = json.dumps(out, ensure_ascii=False)
    if DASHES.search(text):
        raise SystemExit("build_accuracy: an em or en dash would reach the website; fix the source text first.")
    return out


def write_summary(check: bool) -> dict[str, Any]:
    summary = verification.summary_from_files()
    text = verification.summary_text(summary)
    path = verification.SUMMARY_PATH
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if check:
        if current != text:
            raise SystemExit("build_accuracy: verification/summary.json is out of date; run scripts/build_accuracy.py")
        print("build_accuracy: verification/summary.json matches both passes")
    elif current != text:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")
    return summary


def _rate(part: int, whole: int) -> str:
    return str((Decimal(part) / Decimal(whole)).quantize(Decimal("0.01"))) if whole else "n/a"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build web/public/data/accuracy.json from real runs.")
    parser.add_argument("--junit", type=Path, help="use this pytest JUnit XML report instead of running pytest")
    parser.add_argument("--summary", action="store_true", help="only rewrite verification/summary.json")
    parser.add_argument("--check-summary", action="store_true", help="exit 1 if summary.json is out of date")
    args = parser.parse_args(argv)
    summary = write_summary(check=bool(args.check_summary))
    if args.summary or args.check_summary:
        return 0
    junit = cast(Path | None, args.junit)
    areas = parse_junit(junit.read_text(encoding="utf-8") if junit else run_pytest())
    out = build(areas, summary, dt.date.today())
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    passed, failed = sum(a.passed for a in areas), sum(a.failed for a in areas)
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT).as_posix()}: tests {passed} passed, {failed} failed")
    for s in cast(list[dict[str, Any]], out["search"]):
        shown = ", ".join(
            f"{m['name']} {m['hits']}/{m['of']} ({_rate(m['hits'], m['of'])})"
            if "hits" in m
            else f"{m['name']} {m['value']}"
            for m in cast(list[dict[str, Any]], s["metrics"])
        )
        print(f"  search {s['set']}: {shown}; {len(cast(list[object], s['misses']))} missed")
    counts = cast(dict[str, int], summary["counts"])
    print(
        f"  verification: {counts['values_confirmed_by_both']} of {counts['values']} values confirmed by both passes; "
        f"{counts['notes_not_confirmed']} notes not confirmed by them; {counts['notes_confirmed_by_every_recheck']} "
        f"of {counts['notes_rechecked']} re-checked notes confirmed by every re-check; "
        f"{counts['recheck_mismatches']} re-check mismatches, {counts['recheck_mismatches_open']} not resolved"
    )
    for problem in open_recheck_problems(summary):
        print(f"  WARNING: {problem}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
