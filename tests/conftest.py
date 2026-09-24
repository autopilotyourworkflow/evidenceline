"""Shared fixtures: the packaged dataset and a small boundary dataset built in a temp directory."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from evidenceline import redact
from evidenceline.dataset import Dataset, default_dataset, load_dataset

HEADER = "lab_report_id,site_id,well_id,sample_id,sample_date,matrix,analyte,result,unit,lor\n"

WALKTHROUGH_WRONG = (
    "PFOS levels in well MB2 increased between November 2024 and September 2025 and are now above the "
    "drinking-water guideline."
)
WALKTHROUGH_RIGHT = "PFOS in well MB2 was slightly lower in September 2025 than in November 2024."


@pytest.fixture(autouse=True)
def isolated_redaction(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Keep every test away from the real ~/.evidenceline: no identifier file, and the audit log in a temp folder.

    Tests that need identifiers set EVIDENCELINE_REDACT themselves. The redaction session is reset around each test.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(redact.CONFIG_ENV, raising=False)
    monkeypatch.setenv(redact.AUDIT_ENV, str(home / "audit.jsonl"))
    redact.reset_session()
    yield home
    redact.reset_session()


@pytest.fixture(scope="session")
def data() -> Dataset:
    return default_dataset()


def write_lab_file(folder: Path, name: str, date: str, report: str, rows: list[tuple[str, str]]) -> None:
    lines = [HEADER]
    for analyte, result in rows:
        lines.append(
            f"{report},FDS-01,MB9,MB9-{date.replace('-', '')},{date},groundwater,{analyte},{result},ug/L,0.001\n"
        )
    (folder / name).write_text("".join(lines), encoding="utf-8")


@pytest.fixture
def boundary_data(tmp_path: Path) -> Dataset:
    """Well MB9: one round where PFOS equals the current limit and the PFOS + PFHxS sum equals the NEMP 3.0 limit."""
    shutil.copy(Path(str(resources.files("evidenceline") / "data" / "guidelines.json")), tmp_path / "guidelines.json")
    write_lab_file(
        tmp_path,
        "mb9_round1_lab.csv",
        "2025-01-10",
        "SYN-250110",
        [("PFOA", "<0.001"), ("PFOS", "0.008"), ("PFHxS", "0.062")],
    )
    write_lab_file(
        tmp_path,
        "mb9_round2_lab.csv",
        "2025-06-10",
        "SYN-250610",
        [("PFOA", "0.004"), ("PFOS", "0.008"), ("PFHxS", "<0.001")],
    )
    return load_dataset(tmp_path)
