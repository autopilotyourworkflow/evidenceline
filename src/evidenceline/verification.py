"""Compare the two independent guideline verification passes (``data/verification/pass-a.json`` and ``pass-b.json``).

Each pass checked every guideline value in ``guidelines.json`` and ``fds01_site/soil_criteria.json`` against its
primary source, without reading the other pass. This module reads both, pairs their records, and writes one
summary (``data/verification/summary.json``) that the Accuracy page is built from. Nothing here judges a value: it
only reports what each pass recorded, and whether the two agree.

Two questions are kept apart, because the passes kept them apart:

- **The value**: number, unit, scenario, document, table and page. Pass B records this as its record ``verdict``
  (notes are judged separately in ``note_check``). Pass A records the number in ``value_confirmed`` and folds a
  wrong note into its ``verdict``; where its reason says "The mismatch is in the note", the value itself counts as
  confirmed and the note as a mismatch. Any other pass A mismatch stays a mismatch.
- **The explanatory note** next to a value: listed when at least one pass did not confirm it, or when the note in
  the data file is no longer the one the passes checked, with both passes' words and the source quote. A note
  changed after verification carries ``checked_note`` (the wording the passes judged) and
  ``"changed_since_verification": true``: the first passes' verdicts are about the old wording, and only a later
  re-check (below) can speak for the new one.

The summary also records the value each pass saw, so a data file changed after verification shows up as
``"verified_value_matches_file": false`` rather than passing silently.

**Re-checks.** A note corrected after the first two passes is judged again by later, independent re-checks
(``data/verification/notes-recheck-*.json``, one file per re-check, each with a ``.md`` note). Each listed note
carries every re-check's verdict on it, with the wording that re-check judged, and ``confirmed_by_every_recheck`` is
true only when every re-check looked at the note, judged the wording the data file holds now, and confirmed it. A
re-check that finds a mismatch never changes the data: it is counted in ``recheck_mismatches`` and listed. Whatever
else a re-check judged (a related note, the server's wording, what a tool returns) is listed under
``recheck_findings``; a finding fixed in code afterwards carries the fix in ``resolution`` (see ``RESOLUTIONS``,
each checked by a test), and its verdict stays as the re-check recorded it. A re-check of a sentence in
``server.py`` records whether that sentence is still there (``judged_wording_unchanged``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
VERIFICATION_DIR = DATA_DIR / "verification"
SUMMARY_PATH = VERIFICATION_DIR / "summary.json"
GUIDELINES_FILE = "guidelines.json"
SOIL_FILE = "fds01_site/soil_criteria.json"

CONFIRMED = "confirmed"
NOTE_ONLY_MARKER = "The mismatch is in the note"
"""Pass A's own words when a record's mismatch concerns only its explanatory note."""

RECHECK_GLOB = "notes-recheck-*.json"
"""Re-check files, read in name order (``notes-recheck-a.json`` is re-check A)."""

_NOTE_LOCATION = re.compile(
    r"^rules\[id=(?P<rule>[^\]]+)\]\.limits\[key=(?P<key>[^\]]+)\]\.note$|^criteria\[id=(?P<crit>[^\]]+)\]\.note$"
)
"""Where a re-check says a note lives, for example 'rules[id=nemp-3.0].limits[key=PFOS+PFHxS].note'."""

RECHECK_SUBJECTS: dict[str, tuple[str, str]] = {
    # A re-check's own id for something that is not a data-file note: (shared id, what it is, in words).
    "server/INSTRUCTIONS": ("server:instructions", "The NEMP 3.0 rule in the MCP server's instructions"),
    "server/lookup_limit docstring": ("server:lookup_limit", "The lookup_limit tool description"),
    "server/lookup_limit": ("server:lookup_limit", "The lookup_limit tool description"),
    "core.lookup_limit returned fields (related, not owned here)": (
        "core:lookup_limit-fields",
        "What lookup_limit returns when asked for PFOS or PFHxS under NEMP 3.0",
    ),
}

RESOLUTIONS: dict[str, str] = {
    "core:lookup_limit-fields": "Fixed after the re-check: for PFOS or PFHxS under NEMP 3.0, the compared quantity "
    "now names that analyte on its own, and the tool and field descriptions no longer promise an either/or answer. "
    "The verdict shown is about the earlier output.",
}
"""Re-check findings fixed in code afterwards. Each claim is checked by a test against the running code."""

RULE_LABELS = {"nemp-3.0": "PFAS NEMP 3.0", "current": "current national values"}
"""Short names for the two drinking-water rules, as the website names them. Neither is preferred."""

Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class FileValue:
    """One guideline value as the data file states it now."""

    key: str
    """Shared id, for example 'water:nemp-3.0:PFOS+PFHxS' or 'soil:S-As-A' (pass B's form)."""
    file: str
    label: str
    value: str
    unit: str
    source: str
    note: str


def _load(path: Path) -> Json:
    return cast(Json, json.loads(path.read_text(encoding="utf-8")))


def file_values(data_dir: Path = DATA_DIR) -> list[FileValue]:
    """Every guideline value in the two data files, in file order."""
    values: list[FileValue] = []
    for rule in cast(list[Json], _load(data_dir / GUIDELINES_FILE)["rules"]):
        rule_id = str(rule["id"])
        where = f"{rule['table']}, {rule['page_basis']} {rule['page']}"
        for limit in cast(list[Json], rule["limits"]):
            key = str(limit["key"])
            what = "PFOS and PFHxS added together" if limit["applies_to"] == "sum" else key
            values.append(
                FileValue(
                    key=f"water:{rule_id}:{key}",
                    file=GUIDELINES_FILE,
                    label=f"{what}, drinking water, {RULE_LABELS.get(rule_id, str(rule['name']))}",
                    value=str(limit["value"]),
                    unit=str(limit["unit"]),
                    source=where,
                    note=str(limit.get("note", "")),
                )
            )
    soil = _load(data_dir / SOIL_FILE)
    for crit in cast(list[Json], soil["criteria"]):
        label = str(crit["label"])
        values.append(
            FileValue(
                key=f"soil:{crit['id']}",
                file=SOIL_FILE,
                label=f"{label[0].upper()}{label[1:]}, soil, {soil['scenario_short']}",
                value=str(crit["value"]),
                unit=str(crit["unit"]),
                source=f"{crit['table']}, {crit['page']}",
                note=str(crit.get("note", "")),
            )
        )
    return values


def pass_a_key(record_id: str) -> str:
    """Pass A ids ('guidelines.json/nemp-3.0/PFOS+PFHxS') in pass B's form ('water:nemp-3.0:PFOS+PFHxS')."""
    for prefix, kind in (("guidelines.json/", "water:"), ("soil_criteria.json/", "soil:")):
        if record_id.startswith(prefix):
            return kind + record_id.removeprefix(prefix).replace("/", ":")
    raise ValueError(f"pass A record id {record_id!r} is not for guidelines.json or soil_criteria.json")


def _same(record_id: str) -> str:
    return record_id


def _index(records: list[Json], key: Callable[[str], str]) -> dict[str, Json]:
    out: dict[str, Json] = {}
    for record in records:
        k = key(str(record["id"]))
        if k in out:
            raise ValueError(f"two records for {k}")
        out[k] = record
    return out


def _value_verdict_a(record: Json) -> tuple[str, str | None]:
    """Pass A's verdict on the value itself, and on the note when the verdict was about the note."""
    verdict = str(record["verdict"])
    note_check = cast(Json | None, record.get("note_check"))
    note = str(note_check["verdict"]) if note_check else None
    if verdict == "mismatch" and record.get("value_confirmed") is True and NOTE_ONLY_MARKER in str(record["reason"]):
        return CONFIRMED, "mismatch"
    return verdict, note


def _verified_value(record: Json) -> str | None:
    says = record.get("file_says")
    return str(cast(Json, says)["value"]) if isinstance(says, dict) and "value" in says else None


def _pass_meta(data: Json, pass_id: str) -> Json:
    method = data["method"]
    text = " ".join(cast(list[str], method)) if isinstance(method, list) else str(method)
    independence = str(data.get("independence", "")) or next(
        (m for m in cast(list[str], method) if "Independent" in m), ""
    )
    return {"id": pass_id, "date": str(data["date"]), "independence": independence, "method": text}


def _checked_note(a: Json, b: Json) -> str | None:
    """The note text the passes judged, as they recorded it (pass B's first, since it quotes the note on its own)."""
    b_check = cast(Json | None, b.get("note_check"))
    if b_check and "file_note" in b_check:
        return str(b_check["file_note"])
    says = a.get("file_says")
    if isinstance(says, dict) and "note" in says:
        return str(cast(Json, says)["note"])
    return None


def _note_entry(value: FileValue, a: Json, b: Json, a_note: str | None) -> Json | None:
    b_check = cast(Json | None, b.get("note_check"))
    b_note = str(b_check["verdict"]) if b_check else None
    checked = _checked_note(a, b)
    changed = checked is not None and checked != value.note
    if not changed and a_note in (None, CONFIRMED) and b_note in (None, CONFIRMED):
        return None
    a_check = cast(Json | None, a.get("note_check"))
    a_reason = str(a_check["reason"]) if a_check else str(a["reason"])
    return {
        "id": value.key,
        "label": value.label,
        "file": value.file,
        "file_note": value.note,
        "checked_note": checked,
        "changed_since_verification": changed,
        "verdicts": {
            "A": {"verdict": a_note or "not checked", "note": a_reason if a_note else ""},
            "B": {"verdict": b_note or "not checked", "note": str(b_check["reason"]) if b_check else ""},
        },
        "source_says": {
            "A": [str(q) for q in cast(list[object], a.get("source_says", []))],
            "B": [str(b_check["source_says"])] if b_check and "source_says" in b_check else [],
        },
    }


def note_key(location: str) -> str:
    """A re-check's note location in the summary's id form: 'water:nemp-3.0:PFOS+PFHxS' or 'soil:S-As-A'."""
    found = _NOTE_LOCATION.match(location)
    if found is None:
        raise ValueError(f"re-check note location {location!r} is not a guidelines.json or soil criteria note")
    if found["crit"] is not None:
        return f"soil:{found['crit']}"
    return f"water:{found['rule']}:{found['key']}"


def _recheck_id(recheck: Json) -> str:
    """Re-check A writes its id as 'pass', re-check B as 'pass_id'."""
    return str(recheck.get("pass_id") or recheck.get("pass") or "")


def _recheck_meta(recheck: Json, file_name: str) -> Json:
    return {
        "id": _recheck_id(recheck),
        "date": str(recheck["date"]),
        "file": file_name,
        "method": str(recheck["method"]),
    }


def _quotes(source_says: object) -> list[str]:
    if isinstance(source_says, dict):
        return [str(v) for v in cast(Json, source_says).values()]
    if isinstance(source_says, list):
        return [str(v) for v in cast(list[object], source_says)]
    return [str(source_says)] if source_says else []


def _recheck_notes(rechecks: list[Json]) -> dict[str, dict[str, Json]]:
    """Every re-check's record on each data-file note, by note id and then re-check id."""
    out: dict[str, dict[str, Json]] = {}
    for recheck in rechecks:
        rid = _recheck_id(recheck)
        for record in cast(list[Json], recheck.get("notes", [])):
            key = note_key(str(record["location"]))
            if rid in out.setdefault(key, {}):
                raise ValueError(f"re-check {rid} has two records for {key}")
            out[key][rid] = {
                "verdict": str(record["verdict"]),
                "note": str(record["reason"]),
                "checked_note": str(record["file_says"]),
                "page": str(record.get("page", "")),
                "source_says": _quotes(record.get("source_says")),
            }
    return out


def _recheck_verdicts(by_recheck: dict[str, Json], recheck_ids: list[str], file_note: str) -> dict[str, Json]:
    """One entry per re-check, "not checked" when it did not look at the note. ``judged_current_note`` says whether
    the wording it judged is the one the data file holds now."""
    verdicts: dict[str, Json] = {}
    for rid in recheck_ids:
        record = by_recheck.get(rid)
        if record is None:
            verdicts[rid] = {"verdict": "not checked", "note": "", "checked_note": None, "judged_current_note": False}
            continue
        verdicts[rid] = {
            "verdict": record["verdict"],
            "note": record["note"],
            "checked_note": record["checked_note"],
            "judged_current_note": record["checked_note"] == file_note,
            "page": record["page"],
            "source_says": record["source_says"],
        }
    return verdicts


def _confirmed_by_every_recheck(verdicts: dict[str, Json]) -> bool:
    return bool(verdicts) and all(v["verdict"] == CONFIRMED and v["judged_current_note"] for v in verdicts.values())


def _recheck_findings(
    rechecks: list[Json], listed: set[str], values: list[FileValue], recheck_notes: dict[str, dict[str, Json]]
) -> list[Json]:
    """What the re-checks judged besides the listed notes: related notes, the server's wording, a tool's output."""
    recheck_ids = [_recheck_id(r) for r in rechecks]
    by_value = {v.key: v for v in values}
    findings: dict[str, Json] = {}
    for key, by_recheck in recheck_notes.items():
        if key in listed:
            continue
        value = by_value.get(key)
        if value is None:
            raise ValueError(f"a re-check judged the note for {key}, which is not in the data files")
        findings[key] = {
            "id": key,
            "subject": f"Note next to: {value.label}",
            "file": value.file,
            "first_passes": "Both first passes confirmed this note, and it has not changed since.",
            "verdicts": {
                rid: {"verdict": v["verdict"], "note": v["note"]}
                for rid, v in _recheck_verdicts(by_recheck, recheck_ids, value.note).items()
            },
            "resolution": RESOLUTIONS.get(key),
        }
    for recheck in rechecks:
        rid = _recheck_id(recheck)
        for record in cast(list[Json], recheck.get("server_wording", [])):
            raw_id = str(record["id"])
            if raw_id not in RECHECK_SUBJECTS:
                raise ValueError(f"re-check {rid}: unknown finding {raw_id!r}; add it to RECHECK_SUBJECTS")
            key, subject = RECHECK_SUBJECTS[raw_id]
            finding = findings.setdefault(
                key,
                {
                    "id": key,
                    "subject": subject,
                    "file": str(record["file"]),
                    "first_passes": None,
                    "verdicts": {},
                    "resolution": RESOLUTIONS.get(key),
                },
            )
            verdicts = cast(dict[str, Json], finding["verdicts"])
            if rid in verdicts:
                raise ValueError(f"re-check {rid} has two records for {key}")
            verdicts[rid] = {
                "verdict": str(record["verdict"]),
                "note": str(record["reason"]),
                "judged_wording_unchanged": _wording_still_in_file(record),
            }
    for finding in findings.values():
        verdicts = cast(dict[str, Json], finding["verdicts"])
        finding["verdicts"] = {
            rid: verdicts.get(rid, {"verdict": "not checked", "note": "Not recorded as a separate finding."})
            for rid in recheck_ids
        }
    unknown = set(RESOLUTIONS) - set(findings)
    if rechecks and unknown:
        raise ValueError(f"RESOLUTIONS names findings no re-check made: {', '.join(sorted(unknown))}")
    return list(findings.values())


def _wording_still_in_file(record: Json) -> bool | None:
    """For a re-check of the server's own words: whether the sentence it judged is still in server.py (spacing and
    line breaks ignored). None when the record judged something other than a quoted sentence of server.py."""
    judged = record.get("file_says") or record.get("text")
    if not str(record.get("file", "")).endswith("evidenceline/server.py") or not judged:
        return None
    source = (PACKAGE_DIR / "server.py").read_text(encoding="utf-8")
    return " ".join(str(judged).split()) in " ".join(source.split())


def _mismatches(notes: list[Json], findings: list[Json]) -> list[tuple[str, str]]:
    """(id, re-check id) for every re-check verdict of "mismatch", on a listed note or any other finding."""
    return [
        (str(item["id"]), rid)
        for item, verdicts in [(n, n["rechecks"]) for n in notes] + [(f, f["verdicts"]) for f in findings]
        for rid, v in cast(dict[str, Json], verdicts).items()
        if v["verdict"] == "mismatch"
    ]


def build_summary(
    pass_a: Json, pass_b: Json, values: list[FileValue], rechecks: list[tuple[str, Json]] | None = None
) -> Json:
    """The comparison of both passes over every value in the data files, with the later re-checks of the notes
    (``(file name, contents)`` pairs). Raises if a value was not checked, or a re-check names something it cannot
    place."""
    a_values = _index(cast(list[Json], pass_a["values"]), pass_a_key)
    a_rules = _index(cast(list[Json], pass_a["rule_checks"]), pass_a_key)
    b_records = _index(cast(list[Json], pass_b["records"]), _same)
    rows: list[Json] = []
    notes: list[Json] = []
    for value in values:
        a, b = a_values.get(value.key), b_records.get(value.key)
        if a is None or b is None:
            missing = " and ".join(p for p, r in (("pass A", a), ("pass B", b)) if r is None)
            raise ValueError(f"{value.key} ({value.label}) is not in {missing}")
        a_verdict, a_note = _value_verdict_a(a)
        b_verdict = str(b["verdict"])
        seen = {_verified_value(a), _verified_value(b)}
        rows.append(
            {
                "id": value.key,
                "label": value.label,
                "file": value.file,
                "value": value.value,
                "unit": value.unit,
                "source": value.source,
                "verdicts": {
                    "A": {"verdict": a_verdict, "note": str(a.get("caveat", ""))},
                    "B": {"verdict": b_verdict, "note": ""},
                },
                "both_confirm": a_verdict == CONFIRMED and b_verdict == CONFIRMED,
                "verified_value_matches_file": seen == {value.value},
            }
        )
        note = _note_entry(value, a, b, a_note)
        if note is not None:
            notes.append(note)
    rules: list[Json] = []
    for key, b in b_records.items():
        if key in {v.key for v in values}:
            continue
        a = a_rules.get(key)
        rules.append(
            {
                "id": key,
                "file_says": b["file_says"],
                "verdicts": {"A": str(a["verdict"]) if a else "not checked", "B": str(b["verdict"])},
            }
        )
    rules.extend(
        {"id": key, "file_says": a["file_says"], "verdicts": {"A": str(a["verdict"]), "B": "not checked"}}
        for key, a in a_rules.items()
        if key not in b_records
    )
    recheck_data = [data for _, data in rechecks or []]
    recheck_ids = [_recheck_id(r) for r in recheck_data]
    if len(set(recheck_ids)) != len(recheck_ids) or "" in recheck_ids:
        raise ValueError(f"re-checks need distinct ids, not {recheck_ids}")
    recheck_notes = _recheck_notes(recheck_data)
    for note in notes:
        by_recheck = recheck_notes.get(str(note["id"]))
        verdicts = {} if by_recheck is None else _recheck_verdicts(by_recheck, recheck_ids, str(note["file_note"]))
        note["rechecks"] = verdicts
        note["confirmed_by_every_recheck"] = _confirmed_by_every_recheck(verdicts)
    findings = _recheck_findings(recheck_data, {str(n["id"]) for n in notes}, values, recheck_notes)
    mismatches = _mismatches(notes, findings)
    resolved = {str(f["id"]) for f in findings if f["resolution"]}
    confirmed_both = sum(1 for r in rows if r["both_confirm"])
    return {
        "_about": "Generated by scripts/build_accuracy.py from pass-a.json, pass-b.json and the notes-recheck files "
        "(evidenceline.verification); do not edit by hand. Independent automated passes, not a review by a "
        "practitioner.",
        "passes": [_pass_meta(pass_a, "A"), _pass_meta(pass_b, "B")],
        "rechecks": [_recheck_meta(data, name) for name, data in rechecks or []],
        "counts": {
            "values": len(rows),
            "values_confirmed_by_both": confirmed_both,
            "values_not_confirmed_by_both": len(rows) - confirmed_both,
            "verified_value_differs_from_file": sum(1 for r in rows if not r["verified_value_matches_file"]),
            "notes_not_confirmed": len(notes),
            "notes_changed_since_verification": sum(1 for n in notes if n["changed_since_verification"]),
            "notes_rechecked": sum(1 for n in notes if n["rechecks"]),
            "notes_confirmed_by_every_recheck": sum(1 for n in notes if n["confirmed_by_every_recheck"]),
            "recheck_findings": len(findings),
            "recheck_mismatches": len(mismatches),
            "recheck_mismatches_open": sum(1 for key, _ in mismatches if key not in resolved),
            "document_and_status_checks": len(rules),
            "document_and_status_checks_confirmed_by_both": sum(
                1 for r in rules if r["verdicts"] == {"A": CONFIRMED, "B": CONFIRMED}
            ),
        },
        "values": rows,
        "notes_not_confirmed": notes,
        "recheck_findings": findings,
        "document_and_status_checks": rules,
    }


def load_rechecks(verification_dir: Path = VERIFICATION_DIR) -> list[tuple[str, Json]]:
    """Every ``notes-recheck-*.json`` file, in name order, as (file name, contents)."""
    return [(path.name, _load(path)) for path in sorted(verification_dir.glob(RECHECK_GLOB))]


def summary_from_files(verification_dir: Path = VERIFICATION_DIR, data_dir: Path = DATA_DIR) -> Json:
    return build_summary(
        _load(verification_dir / "pass-a.json"),
        _load(verification_dir / "pass-b.json"),
        file_values(data_dir),
        load_rechecks(verification_dir),
    )


def summary_text(summary: Json) -> str:
    """The summary as written to disk: sorted keys are not used, so the file reads in a sensible order."""
    return json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
