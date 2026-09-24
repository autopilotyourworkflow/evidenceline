"""Independently compute the expected QA outcome for the synthetic site FDS-01.

This is a second, deliberately plain implementation: standard library only, exact fractions, no import from
``evidenceline``. The tests compare the tidy engine with it, and with hand-checked numbers. Run it to print the
expected outcome:

    .venv/Scripts/python scripts/expected_tidy.py
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import sys
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

SITE = Path(__file__).resolve().parents[1] / "src" / "evidenceline" / "data" / "fds01_site"

# unit spelling (lower case, micro sign and Greek mu read as u) -> (matrix, factor to ug/L or mg/kg)
UNITS = {
    "ng/l": ("water", Fraction(1, 1000)),
    "ug/l": ("water", Fraction(1)),
    "mg/l": ("water", Fraction(1000)),
    "mg/kg": ("soil", Fraction(1)),
}
# Soil HIL A (NEPM B1 Table 1A(1); NEMP 3.1 Table 5) and drinking water (guidelines.json), exact.
SOIL = {"PFOS+PFHxS": Fraction(3, 1000), "PFOA": Fraction(6, 100), "Cadmium": Fraction(20), "Mercury": Fraction(40)}
WATER = {
    "nemp-3.0": {"PFOS+PFHxS": Fraction(7, 100), "PFOA": Fraction(56, 100)},
    "current": {"PFOS": Fraction(8, 1000), "PFHxS": Fraction(3, 100), "PFOA": Fraction(2, 10), "PFBS": Fraction(1)},
}
HOLDING_DAYS = {("soil", "Mercury"): 28, ("soil", "PFAS"): 28, ("water", "PFAS"): 14}
PFAS = {"PFOS", "PFHxS", "PFOA", "PFHxA"}


def read(name: str) -> list[tuple[int, dict[str, str]]]:
    """Rows with their line numbers; '#' lines before the header are skipped."""
    lines = (SITE / name).read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return [(start + 2 + i, {k: v.strip() for k, v in row.items()}) for i, row in enumerate(reader)]


def unit_key(text: str) -> str:
    return text.replace("µ", "u").replace("μ", "u").lower()


def lab_date(text: str) -> dt.date:
    return dt.datetime.strptime(text[:11], "%d %b %Y").date()


def compute() -> dict[str, Any]:
    lab = read("lab_results.csv")
    coc = read("chain_of_custody.csv")
    field = read("field_sheet.csv")
    rows: list[dict[str, Any]] = []
    for line, raw in lab:
        matrix, factor = UNITS[unit_key(raw["unit"])]
        nd = raw["prefix"] == "<"
        number = Fraction(raw["result"]) * factor
        rows.append(
            {
                "line": line,
                "id": raw["sample_id"],
                "analyte": raw["analyte"],
                "matrix": matrix,
                "unit_key": unit_key(raw["unit"]),
                "raw": Fraction(raw["result"]),
                "value": None if nd else number,
                "lor": Fraction(raw["lor"]) * UNITS[unit_key(raw["lor_unit"])][1],
                "sampled": lab_date(raw["sampled"]),
                "extracted": lab_date(raw["extracted"]),
            }
        )
    by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(row["id"], {})[row["analyte"]] = row
    coc_ids = {raw["sample_id"]: raw for _, raw in coc}
    field_rows = {raw["sample_id"]: raw for _, raw in field}

    # 1. sample ids
    lab_only = sorted(i for i in by_id if i not in coc_ids)
    held = sorted(i for i, raw in coc_ids.items() if i not in by_id and raw["hold"] == "Y")
    no_results = sorted(i for i, raw in coc_ids.items() if i not in by_id and raw["hold"] != "Y")

    # 2. units: majority per matrix
    unit_flags: dict[str, list[str]] = {}
    for matrix in ("soil", "water"):
        keys = [r["unit_key"] for r in rows if r["matrix"] == matrix]
        majority = max(set(keys), key=keys.count)
        for r in rows:
            if r["matrix"] == matrix and r["unit_key"] != majority:
                unit_flags.setdefault(r["id"], []).append(r["analyte"])

    # 3. duplicates
    rpd: dict[tuple[str, str], tuple[Fraction | None, str]] = {}
    for _, raw in field:
        if raw["qa_type"] != "field_duplicate":
            continue
        parent, dup = raw["parent_sample"], raw["sample_id"]
        for analyte, p in by_id[parent].items():
            d = by_id[dup][analyte]
            if p["value"] is None and d["value"] is None:
                rpd[(parent, analyte)] = (None, "both not detected")
                continue
            a = p["value"] if p["value"] is not None else p["lor"]
            b = d["value"] if d["value"] is not None else d["lor"]
            value = abs(a - b) / ((a + b) / 2) * 100
            lor = max(p["lor"], d["lor"])
            if (a + b) / 2 < 10 * lor:
                verdict = "no limit"
            elif value > 50:
                verdict = "investigate"
            elif value > 30:
                verdict = "review"
            else:
                verdict = "acceptable"
            rpd[(parent, analyte)] = (value, verdict)
    mb2, qc2 = by_id["MB2"]["PFOS"], by_id["QC2"]["PFOS"]
    naive = abs(mb2["raw"] - qc2["raw"]) / ((mb2["raw"] + qc2["raw"]) / 2) * 100

    # 4. holding times (other metals: 6 months, far longer than any gap here)
    days = {(r["id"], r["analyte"]): (r["extracted"] - r["sampled"]).days for r in rows}
    breaches: list[tuple[str, str, int, int]] = []
    for r in rows:
        group = "PFAS" if r["analyte"] in PFAS else r["analyte"]
        limit = HOLDING_DAYS.get((r["matrix"], group))
        if limit is not None and days[(r["id"], r["analyte"])] > limit:
            breaches.append((r["id"], r["analyte"], days[(r["id"], r["analyte"])], limit))

    # 5. blanks
    blank_types = {"rinsate_blank", "field_blank", "trip_blank", "container_blank"}
    detections: list[tuple[str, str, Fraction, Fraction]] = []
    linked: dict[str, list[str]] = {}
    marginal: list[tuple[str, str, Fraction, Fraction]] = []
    for sample, raw in field_rows.items():
        if raw["qa_type"] not in blank_types:
            continue
        for analyte, r in by_id[sample].items():
            if r["value"] is not None:
                detections.append((sample, analyte, r["value"], r["value"] / r["lor"]))
                if raw["qa_type"] == "rinsate_blank":
                    linked[sample] = [
                        s
                        for s, f in field_rows.items()
                        if f["qa_type"] not in blank_types
                        and f["equipment"] == raw["equipment"]
                        and f["date"] == raw["date"]
                    ]
    for blank, samples in linked.items():
        for s in samples:
            res = by_id.get(s, {})
            if (
                "PFOS" in res
                and "PFHxS" in res
                and res["PFOS"]["value"] is not None
                and res["PFHxS"]["value"] is not None
            ):
                total = res["PFOS"]["value"] + res["PFHxS"]["value"]
                if SOIL["PFOS+PFHxS"] < total <= Fraction(3, 2) * SOIL["PFOS+PFHxS"]:
                    marginal.append((blank, s, total, total / SOIL["PFOS+PFHxS"]))

    # 6. LOR against criteria (non-blank non-detects)
    not_confirmed: list[tuple[str, str, str]] = []
    for sample, res in by_id.items():
        if field_rows.get(sample, {}).get("qa_type") in blank_types:
            continue
        matrix = next(iter(res.values()))["matrix"]
        rules = {"HIL A": SOIL} if matrix == "soil" else WATER
        for rule, values in rules.items():
            for key, limit in values.items():
                members = key.split("+")
                if any(m not in res for m in members) or all(res[m]["value"] is not None for m in members):
                    continue
                low = sum((res[m]["value"] or Fraction(0) for m in members), Fraction(0))
                high = sum(
                    (res[m]["value"] if res[m]["value"] is not None else res[m]["lor"] for m in members), Fraction(0)
                )
                if low <= limit < high:
                    not_confirmed.append((sample, key, rule))
                # NEMP Table 4 and Table 5, footnote a: a PFOS + PFHxS value also applies to each of the two alone.
                for m in members if len(members) > 1 else []:
                    if res[m]["value"] is None and res[m]["lor"] > limit:
                        not_confirmed.append((sample, m, rule))

    return {
        "results": len(rows),
        "soil": sum(r["matrix"] == "soil" for r in rows),
        "water": sum(r["matrix"] == "water" for r in rows),
        "samples": len(by_id),
        "lab_only": lab_only,
        "coc_without_results_not_held": no_results,
        "held": held,
        "unit_flags": unit_flags,
        "mb2_ug_per_l": {a: (r["value"], r["lor"]) for a, r in by_id["MB2"].items()},
        "rpd": rpd,
        "naive_mb2_pfos_rpd": naive,
        "holding_days": days,
        "holding_breaches": breaches,
        "blank_detections": detections,
        "blank_linked": linked,
        "marginal": marginal,
        "lor_not_confirmed": not_confirmed,
    }


def _show(value: object) -> str:
    if isinstance(value, Fraction):
        exact = Decimal(value.numerator) / Decimal(value.denominator)
        return str(exact.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    return str(value)


def main() -> None:
    expected = compute()
    for key, value in expected.items():
        if key in ("rpd", "holding_days"):
            print(f"{key}:")
            for sub, item in cast(dict[object, object], value).items():
                parts = cast(tuple[object, ...], item) if isinstance(item, tuple) else (item,)
                print(f"  {sub}: {', '.join(_show(part) for part in parts)}")
        else:
            print(f"{key}: {value}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
