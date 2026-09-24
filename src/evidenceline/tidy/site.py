"""Load one site's three files, its settings and its soil values into a :class:`SiteData`.

Only packaged sites are available (``FDS-01``, synthetic). Reading an arbitrary local folder is a later feature;
:func:`load_site_folder` exists so tests can build small sites of their own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, cast

from evidenceline.errors import EvidencelineError
from evidenceline.tidy.criteria import Criterion, load_soil_criteria, water_criteria
from evidenceline.tidy.files import Table, read_table
from evidenceline.tidy.records import (
    COC_COLUMNS,
    COC_FILE,
    FIELD_COLUMNS,
    FIELD_FILE,
    LAB_COLUMNS,
    LAB_FILE,
    CocRow,
    FieldRow,
    LabRow,
    coc_rows,
    field_rows,
    lab_rows,
)

SITES: dict[str, str] = {"FDS-01": "fds01_site"}
"""Packaged sites: site id -> folder under ``evidenceline/data``."""


@dataclass(frozen=True, slots=True)
class SiteData:
    site_id: str
    name: str
    synthetic: str
    soil_scenario: str
    water_scenario: str
    tables: Mapping[str, Table]
    lab: tuple[LabRow, ...]
    coc: tuple[CocRow, ...]
    field: tuple[FieldRow, ...]
    criteria: tuple[Criterion, ...]

    def field_row(self, sample_id: str) -> FieldRow | None:
        return next((row for row in self.field if row.sample_id == sample_id), None)

    def coc_row(self, sample_id: str) -> CocRow | None:
        return next((row for row in self.coc if row.sample_id == sample_id), None)

    def lab_samples(self) -> dict[str, list[LabRow]]:
        """Lab rows grouped by the sample id the lab wrote, in file order."""
        grouped: dict[str, list[LabRow]] = {}
        for row in self.lab:
            grouped.setdefault(row.sample_id, []).append(row)
        return grouped

    def criteria_for(self, matrix: str) -> tuple[Criterion, ...]:
        return tuple(criterion for criterion in self.criteria if criterion.matrix == matrix)

    @property
    def has_private_header(self) -> bool:
        """True when the field sheet header names a client or an address (never copied into output)."""
        header = self.tables[FIELD_FILE].preamble
        return any(key.startswith(("client", "site address", "address")) for key in header)


def _read(folder: Traversable, name: str) -> str:
    item = folder / name
    if not item.is_file():
        raise EvidencelineError(f"The site folder has no {name}.")
    return item.read_text(encoding="utf-8")


def load_site_folder(folder: Path | Traversable) -> SiteData:
    """Read ``lab_results.csv``, ``chain_of_custody.csv``, ``field_sheet.csv``, ``site.json`` and
    ``soil_criteria.json`` from ``folder``."""
    try:
        settings = cast(dict[str, Any], json.loads(_read(folder, "site.json")))
        site_id = str(settings["site_id"])
        name, synthetic = str(settings["name"]), str(settings["synthetic"])
        soil_scenario, water_scenario = str(settings["soil_scenario"]), str(settings["water_scenario"])
    except (KeyError, ValueError) as exc:
        raise EvidencelineError(f"site.json could not be read: {exc}") from exc
    tables = {
        LAB_FILE: read_table(LAB_FILE, _read(folder, LAB_FILE), LAB_COLUMNS),
        COC_FILE: read_table(COC_FILE, _read(folder, COC_FILE), COC_COLUMNS),
        FIELD_FILE: read_table(FIELD_FILE, _read(folder, FIELD_FILE), FIELD_COLUMNS),
    }
    soil, _ = load_soil_criteria(folder / "soil_criteria.json")
    return SiteData(
        site_id=site_id,
        name=name,
        synthetic=synthetic,
        soil_scenario=soil_scenario,
        water_scenario=water_scenario,
        tables=tables,
        lab=lab_rows(tables[LAB_FILE]),
        coc=coc_rows(tables[COC_FILE]),
        field=field_rows(tables[FIELD_FILE]),
        criteria=soil + water_criteria(),
    )


def resolve_site(site: str) -> str:
    wanted = site.strip().upper()
    for known in SITES:
        if known.upper() == wanted:
            return known
    raise EvidencelineError(
        f"Unknown site {site!r}. Sites available: {', '.join(SITES)} (synthetic). Reading your own folder of lab "
        "files is not available yet."
    )


@cache
def packaged_site(site: str) -> SiteData:
    """A packaged site, loaded once. ``site`` must already be resolved."""
    return load_site_folder(resources.files("evidenceline") / "data" / SITES[site])
