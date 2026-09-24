"""The redaction guard rail on the real server, over the real MCP protocol (a stdio subprocess).

The FDS-01 field sheet names a fictional client and site address in its header. These tests read both from the file
itself and prove neither ever appears in any tool output: not in normal results, and not when a call tries to make
a tool echo them back (as a site id, a well id, inside a paragraph or a question, or in an argument the server
rejects). Every identifier here is fictional.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from evidenceline import redact
from evidenceline.server import mcp as server
from evidenceline.tooling import guarded_tool

from .test_server import isolated_env, stdio_params, structured

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _field_sheet_header(label: str) -> str:
    text = (resources.files("evidenceline") / "data" / "fds01_site" / "field_sheet.csv").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(f"# {label}: "):
            return line.removeprefix(f"# {label}: ").removesuffix(" (fictional)")
    raise AssertionError(f"no '{label}' line in the field sheet header")


CLIENT = _field_sheet_header("Client")
ADDRESS = _field_sheet_header("Site address")
FORBIDDEN = (CLIENT, ADDRESS, *CLIENT.split(), "Example Road", "Welshpool")
"""The whole identifiers and their distinctive parts: none may appear anywhere in tool output."""

HOSTILE_TEXT = f"PFOS at the {CLIENT} site, {ADDRESS}, was {{PFOS|MB2|Sep 2025}} in September 2025."


def test_identifiers_are_read_from_the_field_sheet() -> None:
    assert CLIENT == "Harbourline Logistics"
    assert ADDRESS == "12 Example Road, Welshpool WA"


@pytest.fixture
def identifier_file(tmp_path: Path) -> Path:
    """The identifier file a consultant would keep locally, listing the client (the address is a built-in match)."""
    path = tmp_path / "redact.toml"
    path.write_text(f'[[client]]\nnames = ["{CLIENT}"]\n', encoding="utf-8")
    return path


def _calls() -> list[tuple[str, dict[str, Any]]]:
    """Every tool, called normally and with the identifiers pushed into every text argument."""
    calls: list[tuple[str, dict[str, Any]]] = [
        ("tidy_lab_files", {}),
        ("tidy_lab_files", {"site": "FDS-01"}),
        *[("get_review_item", {"site": "FDS-01", "number": n}) for n in range(1, 7)],
        ("get_results", {"well": "MB2"}),
        ("lookup_limit", {"analyte": "PFOS", "rule": "current"}),
        ("compare_rules", {"well": "MB2", "date": "Sep 2025"}),
        ("fill_numbers", {"text": "PFOS was {PFOS|MB2|Sep 2025} in September 2025.", "well": "MB2"}),
        ("search_guidelines", {"question": "What must a detailed site investigation report include?"}),
        # Hostile: each of these would echo an identifier back if nothing stopped it.
        ("tidy_lab_files", {"site": f"{CLIENT}, {ADDRESS}"}),
        ("get_review_item", {"site": CLIENT, "number": 1}),
        ("get_results", {"well": ADDRESS}),
        ("get_results", {"well": "MB2", "analyte": CLIENT}),
        ("lookup_limit", {"analyte": CLIENT, "rule": ADDRESS}),
        ("compare_rules", {"well": "MB2", "date": ADDRESS}),
        ("check_paragraph", {"text": HOSTILE_TEXT.replace("{PFOS|MB2|Sep 2025}", "0.038 ug/L"), "well": "MB2"}),
        ("fill_numbers", {"text": HOSTILE_TEXT, "well": "MB2"}),
        ("fill_numbers", {"text": f"{{PFOS|{CLIENT}|Sep 2025}}", "well": "MB2"}),
        ("search_guidelines", {"question": f"Reporting duties for {CLIENT} at {ADDRESS}"}),
        ("get_results", {"well": "MB2", "client": CLIENT}),  # rejected argument: the SDK's error quotes the value
        ("show_redactions", {}),
    ]
    return calls


def _dump(result: CallToolResult) -> str:
    return result.model_dump_json()


def _assert_clean(outputs: list[str]) -> None:
    for output in outputs:
        for fragment in FORBIDDEN:
            assert fragment.casefold() not in output.casefold(), (fragment, output[:300])


async def _run_all(client: Client) -> tuple[list[str], dict[str, Any]]:
    outputs: list[str] = []
    filled_text: str | None = None
    for name, arguments in _calls():
        result = await client.call_tool(name, arguments)
        outputs.append(_dump(result))
        if name == "fill_numbers" and arguments["text"] == HOSTILE_TEXT:
            filled = structured(result)
            filled_text = str(filled["text"])
            assert filled_text.startswith("PFOS at the [CLIENT-1] site, [ADDRESS-1], was 0.038 ug/L")
            for value in filled["values"]:  # offsets match the redacted text the model sees
                assert filled_text[value["start"] : value["end"]] == value["text"]
    assert filled_text is not None
    # The redacted paragraph round-trips through the checker with its placeholders intact.
    checked = await client.call_tool("check_paragraph", {"text": filled_text, "well": "MB2"})
    outputs.append(_dump(checked))
    summary = await client.call_tool("show_redactions", {})
    outputs.append(_dump(summary))
    return outputs, structured(summary)


async def test_identifiers_never_reach_the_model_over_stdio(identifier_file: Path) -> None:
    params = stdio_params(**{redact.CONFIG_ENV: str(identifier_file)})
    async with Client(params, read_timeout_seconds=60) as client:
        outputs, summary = await _run_all(client)
    _assert_clean(outputs)
    joined = "\n".join(outputs)
    assert "[CLIENT-1]" in joined
    assert "[ADDRESS-1]" in joined
    # One replacement per hostile argument: arguments are redacted before the tool runs, so echoes and errors
    # already carry the placeholder and nothing is counted twice.
    assert summary["replacements_by_type"] == {"ADDRESS": 7, "CLIENT": 9}
    assert summary["identities_configured"] == {"CLIENT": 1}


async def test_normal_calls_carry_no_identifiers_even_without_an_identifier_file() -> None:
    """With no identifier file the tidy tool still leaves the client and address out of its output."""
    normal = [(name, args) for name, args in _calls() if not any(f in json.dumps(args) for f in FORBIDDEN)]
    async with Client(stdio_params(), read_timeout_seconds=60) as client:
        outputs = [_dump(await client.call_tool(name, arguments)) for name, arguments in normal]
        # The address alone is still caught by the built-in pattern.
        echoed = await client.call_tool("get_results", {"well": ADDRESS})
    _assert_clean([*outputs, _dump(echoed)])
    block = echoed.content[0]
    assert isinstance(block, TextContent)
    assert "[ADDRESS-1]" in block.text


async def test_audit_log_counts_without_values(identifier_file: Path) -> None:
    params = stdio_params(**{redact.CONFIG_ENV: str(identifier_file)})
    async with Client(params, read_timeout_seconds=60) as client:
        await client.call_tool("tidy_lab_files", {"site": CLIENT})
        await client.call_tool("get_results", {"well": "MB2"})
    lines = [json.loads(line) for line in Path(isolated_env()[redact.AUDIT_ENV]).read_text("utf-8").splitlines()]
    assert [line["tool"] for line in lines] == ["tidy_lab_files", "get_results"]  # one line per call
    assert lines[0]["counts"] == {"CLIENT": 1}  # the argument; its echo in the error already holds the placeholder
    assert lines[1]["counts"] == {}
    _assert_clean([json.dumps(lines)])


async def test_missing_identifier_file_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A named identifier file that is missing stops every tool; nothing is returned unredacted."""
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / f"{CLIENT} identifiers.toml"))
    redact.reset_session()
    async with Client(server) as client:
        for name, arguments in _calls()[:12]:
            result = await client.call_tool(name, arguments)
            assert result.is_error, name
            block = result.content[0]
            assert isinstance(block, TextContent)
            assert "No redaction file" in block.text
            assert "No output was returned" in block.text
            _assert_clean([_dump(result)])


class _Note(BaseModel):
    text: str


def _leaky_note() -> _Note:
    """A stand-in tool whose own result (not an argument) carries a site address."""
    return _Note(text=f"Sampled at {ADDRESS} (Lot 12 on Deposited Plan 34567).")


async def test_results_are_redacted_not_only_arguments() -> None:
    async with Client(MCPServer(name="leaky", tools=[guarded_tool(_leaky_note, "Leaky note")])) as client:
        result = await client.call_tool("_leaky_note", {})
    assert result.structured_content == {"text": "Sampled at [ADDRESS-1] ([LOT-1])."}
    _assert_clean([_dump(result)])
