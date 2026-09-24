"""The website's data files must match what the package's own code returns (scripts/export_web_data.py --check)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_web_data_files_are_up_to_date() -> None:
    script = REPO_ROOT / "scripts" / "export_web_data.py"
    done = subprocess.run(
        [sys.executable, str(script), "--check"], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "match the package" in done.stdout
