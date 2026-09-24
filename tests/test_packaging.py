"""Packaging: the stdio server needs only mcp and pydantic; the web API's packages are an optional extra."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_runtime_dependencies_are_only_mcp_and_pydantic() -> None:
    deps = cast(list[str], _pyproject()["project"]["dependencies"])
    assert sorted(d.split(">")[0].split("=")[0] for d in deps) == ["mcp", "pydantic"]


def test_api_extra_and_api_group_list_the_same_packages() -> None:
    data = _pyproject()
    extra = cast(list[str], data["project"]["optional-dependencies"]["api"])
    group = cast(list[str], data["dependency-groups"]["api"])
    assert extra == group
    assert {d.split(">")[0] for d in extra} == {"fastapi", "uvicorn", "anthropic"}


def test_stdio_server_does_not_import_the_api_packages() -> None:
    code = (
        "import sys, evidenceline.server, evidenceline.answer; "
        "print(','.join(m for m in ('fastapi', 'anthropic') if m in sys.modules))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO_ROOT)
    assert done.stdout.strip() == ""
