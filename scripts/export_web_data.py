"""Write the website's data files from the Python package itself, so the site shows exactly what the tools return.

Usage::

    .venv/Scripts/python scripts/export_web_data.py           write the files
    .venv/Scripts/python scripts/export_web_data.py --check   exit 1 if any file is out of date

Writes to ``web/public/data/``:

- ``sample-site.json``: well MB2's four rounds, PFOS and PFHxS with lab report, file and row, and the other analytes.
  Same shape as ``web/scripts/build-data.mjs`` produces (the two must agree byte for byte).
- ``sources.json``: both drinking-water rules with document, table, page, WA status and links.
- ``tidy.json``: a short version of ``tidy_lab_files`` for FDS-01 (review items, checks, not checked; no row table).
- ``search-example.json``: three ``search_guidelines`` answers. Needs the local guidance index; without it this
  file is left as it is and the script says so.

Every value is a string exactly as the tools give it; nothing is parsed into a float. The script fails if the
fictional client name or site address from the FDS-01 field sheet appears in anything it writes.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from evidenceline.dataset import Dataset, LabResult, default_dataset
from evidenceline.guidance.search import default_index_path, search_guidelines
from evidenceline.redact import RedactionConfig, Redactor
from evidenceline.tidy.engine import tidy_lab_files
from evidenceline.units import CANONICAL_UNIT, fmt

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "web" / "public" / "data"
DATA_DIR = REPO_ROOT / "src" / "evidenceline" / "data"
HEADLINE = ("PFOS", "PFHxS")
GENERATED_BY = "scripts/export_web_data.py"

SOURCE_LINKS: dict[str, list[dict[str, str]]] = {
    "nemp-3.0": [
        {
            "label": "PFAS NEMP 3.0 (PDF, archived copy of the DCCEEW file)",
            "url": "https://web.archive.org/web/20250315173805/https://www.dcceew.gov.au/sites/default/files/documents/"
            "pfas-nemp-3.pdf",
        },
    ],
    "current": [
        {
            "label": "PFAS NEMP 3.1 (PDF)",
            "url": "https://www.dcceew.gov.au/sites/default/files/documents/pfas-nemp-3-1.pdf",
        },
        {
            "label": "NHMRC drinking-water guideline fact sheet for PFAS",
            "url": "https://guidelines.nhmrc.gov.au/australian-drinking-water-guidelines/part-5/"
            "physical-chemical-characteristics/cas-numbers-1763-23-1-pfos-335-67-1-pfoa-355-46-4-pfhxs",
        },
    ],
}
"""Links to the primary sources, as recorded in the verified criteria research (2026-09-23). Keep in step with
``web/scripts/build-data.mjs``."""

SEARCH_EXAMPLES = (
    "What must a detailed site investigation report include?",
    "What is a tier 1 screening assessment?",
    "What does the NSW EPA sampling design guideline require?",
)
"""Two questions the corpus covers, and one it does not (another state's guideline)."""


class ExportError(Exception):
    """The data could not be exported as the website expects it."""


def _result(result: LabResult) -> dict[str, Any]:
    return {
        "analyte": result.analyte,
        "reported": result.reported,
        "detected": result.detected,
        "detection_limit": fmt(result.detection_limit),
        "unit": CANONICAL_UNIT,
        "row": result.row,
    }


def build_sample_site(ds: Dataset) -> dict[str, Any]:
    wells = sorted({result.well for result in ds.results})
    if len(wells) != 1:
        raise ExportError(f"expected one well, found {', '.join(wells)}")
    rounds: list[dict[str, Any]] = []
    for number, round_ in enumerate(ds.rounds(wells[0]), start=1):
        rows = sorted(round_.results.values(), key=lambda r: r.row)
        first = rows[0]
        for key in ("sample_id", "site", "matrix"):
            if len({getattr(r, key) for r in rows}) != 1:
                raise ExportError(f"{round_.file}: more than one {key}")
        rounds.append(
            {
                "round": number,
                "date": round_.date.isoformat(),
                "sample_id": first.sample_id,
                "lab_report_id": round_.lab_report,
                "site_id": first.site,
                "well_id": round_.well,
                "matrix": first.matrix,
                "file": round_.file,
                "path": (DATA_DIR / round_.file).relative_to(REPO_ROOT).as_posix(),
                "results": [_result(round_.results[analyte]) for analyte in HEADLINE],
                "other_analytes": [_result(r) for r in rows if r.analyte not in HEADLINE],
            }
        )
    sites = {r["site_id"] for r in rounds}
    if len(sites) != 1:
        raise ExportError("expected one site across all rounds")
    return {
        "synthetic": True,
        "synthetic_notice": "Synthetic data. FDS-01 is a made-up site and these are made-up lab results, created to "
        "show how Evidenceline works.",
        "site_id": sites.pop(),
        "site_description": "A fictional former depot, Perth",
        "well_id": wells[0],
        "unit": CANONICAL_UNIT,
        "row_numbering": "The header is row 1, as in a spreadsheet.",
        "rounds": rounds,
    }


def build_sources(ds: Dataset) -> dict[str, Any]:
    raw = json.loads((resources.files("evidenceline") / "data" / "guidelines.json").read_text(encoding="utf-8"))
    about = str(raw["_about"])
    verified = re.search(r"Verified (\d{4}-\d{2}-\d{2})", about)
    if verified is None:
        raise ExportError("guidelines.json: _about does not state a verification date")
    return {
        "verified_on": verified.group(1),
        "about": about,
        "choice_note": "Evidenceline shows both rules side by side and does not choose between them. Which rule a "
        "report uses is the scientist's call.",
        "rules": [
            {
                "id": rule.id,
                "name": rule.name,
                "document": rule.document,
                "table": rule.table,
                "page": rule.page,
                "page_basis": rule.page_basis,
                "wa_status": rule.wa_status,
                "links": SOURCE_LINKS.get(rule.id, []),
                "limits": [
                    {
                        "key": limit.key,
                        "applies_to": limit.applies_to,
                        "members": list(limit.members),
                        "value": fmt(limit.value),
                        "unit": CANONICAL_UNIT,
                        "scenario": limit.scenario,
                        "note": limit.note,
                    }
                    for limit in rule.limits
                ],
            }
            for rule in ds.rules.values()
        ],
    }


def build_tidy() -> dict[str, Any]:
    result = tidy_lab_files("FDS-01")
    return {
        "generated_by": f"{GENERATED_BY}, from tidy_lab_files(site='FDS-01')",
        "site": result.site,
        "synthetic": result.synthetic,
        "summary": result.summary,
        "files": [file.model_dump() for file in result.files],
        "samples": result.samples,
        "results": result.results,
        "review_items": [
            item.model_dump(include={"number", "check", "title", "found", "samples", "rule", "source"})
            | {
                "scientist_decides": item.scientist_decides,
                "evidence": [f"{row.file} row {row.row}" for row in item.evidence],
            }
            for item in result.review_items
        ],
        "checks": [
            check.model_dump(include={"check", "what_was_checked", "checked", "review_items"})
            for check in result.checks
        ],
        "checked_not_flagged": [{"check": c.check, "what": c.what} for c in result.checked_not_flagged],
        "not_checked": [item.model_dump() for item in result.not_checked],
        "notes": result.notes,
    }


def build_search() -> dict[str, Any] | None:
    if not default_index_path().exists():
        return None
    answers = [search_guidelines(question, 2).model_dump(exclude={"searched_terms"}) for question in SEARCH_EXAMPLES]
    return {
        "generated_by": f"{GENERATED_BY}, from search_guidelines(question, k=2) on the local index",
        "note": "Example answers from the indexed public guidance. Excerpts are short, with each document's licence "
        "notice. Never read a guideline value from an excerpt; open the cited page.",
        "answers": answers,
    }


def _field_sheet_identifiers() -> list[str]:
    text = (DATA_DIR / "fds01_site" / "field_sheet.csv").read_text(encoding="utf-8")
    found = re.findall(r"^# (?:Client|Site address): (.+?)(?: \(fictional\))?$", text, flags=re.MULTILINE)
    if len(found) != 2:
        raise ExportError("field_sheet.csv: expected a Client and a Site address line in the header")
    return found


def render(data: dict[str, Any]) -> str:
    """JSON as ``JSON.stringify(data, null, 2)`` writes it, with a final newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def outputs() -> tuple[dict[str, str], list[str]]:
    ds = default_dataset()
    built: dict[str, dict[str, Any]] = {
        "sample-site.json": build_sample_site(ds),
        "sources.json": build_sources(ds),
        "tidy.json": build_tidy(),
    }
    skipped: list[str] = []
    search = build_search()
    if search is None:
        skipped.append("search-example.json (no guidance index: run scripts/fetch_corpus.py, scripts/build_index.py)")
    else:
        built["search-example.json"] = search
    # The redaction guard rail, with the built-in patterns only and no audit log: it must change nothing.
    redactor = Redactor(RedactionConfig((), "export"), None)
    texts = {name: render(data) for name, data in built.items()}
    for name, text in texts.items():
        if redactor.redact_text(text) != text:
            raise ExportError(f"{name}: a redaction pattern matched; check the data before publishing it")
        for identifier in _field_sheet_identifiers():
            if identifier.casefold() in text.casefold():
                raise ExportError(f"{name}: contains an identifier from the FDS-01 field sheet")
    return texts, skipped


def main(argv: list[str]) -> int:
    check = "--check" in argv
    try:
        texts, skipped = outputs()
    except ExportError as exc:
        print(f"export_web_data: {exc}", file=sys.stderr)
        return 1
    stale = 0
    for name, text in texts.items():
        path = OUT_DIR / name
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
        if check:
            if current != text:
                print(f"export_web_data: {name} is out of date; run scripts/export_web_data.py", file=sys.stderr)
                stale += 1
        elif current != text:
            path.write_bytes(text.encode("utf-8"))
            print(f"wrote web/public/data/{name}")
        else:
            print(f"unchanged web/public/data/{name}")
    for line in skipped:
        print(f"skipped {line}")
    if check and not stale:
        print(f"export_web_data: {len(texts)} files match the package")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
