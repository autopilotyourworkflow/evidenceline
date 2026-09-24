"""Protocol-level tests: talk to the real MCP server through the SDK's own client.

Two transports are covered: the SDK's in-process connection (``Client(server)``) and a real subprocess over
stdio started with the same command a user would configure (``python -m evidenceline``). Every tool is listed and
called, including the drafting flow a model is told to follow: fill_numbers on a template, then check_paragraph.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent

from evidenceline import redact
from evidenceline.guidance.search import default_index_path
from evidenceline.server import INSTRUCTIONS
from evidenceline.server import mcp as server
from evidenceline.tooling import UNKNOWN_PROMPT, UNKNOWN_RESOURCE

from .conftest import WALKTHROUGH_RIGHT, WALKTHROUGH_WRONG

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "tidy_lab_files",
    "get_review_item",
    "get_results",
    "lookup_limit",
    "compare_rules",
    "check_paragraph",
    "fill_numbers",
    "search_guidelines",
    "show_redactions",
}
REPO_ROOT = Path(__file__).resolve().parents[1]
DASHES = ("\u2013", "\u2014")

TEMPLATE = (
    "PFOS was {PFOS|MB2|Sep 2025} in September 2025, above the current guideline of {limit|PFOS|current}. "
    "The sum of PFOS and PFHxS was {sum PFOS+PFHxS|MB2|Sep 2025} in September 2025, below the NEMP 3.0 guideline "
    "of {limit|PFOS+PFHxS|nemp-3.0}. PFOS showed {change|PFOS|MB2|Nov 2024|Sep 2025} between November 2024 and "
    "September 2025. PFOA was not detected in September 2025 ({PFOA|MB2|Sep 2025})."
)
"""A paragraph written the way the server instructions ask: every number is a placeholder."""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    content: object = result.structured_content
    assert isinstance(content, dict)
    return cast(dict[str, Any], content)


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def _exercise(client: Client) -> None:
    listed = await client.list_tools()
    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.title
        assert tool.description
        assert len(tool.description) > 80
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.input_schema.get("additionalProperties") is False
    assert set(tools["check_paragraph"].input_schema["required"]) == {"text", "well"}
    assert set(tools["compare_rules"].input_schema["required"]) == {"well", "date"}
    assert set(tools["fill_numbers"].input_schema["required"]) == {"text", "well"}
    assert set(tools["get_review_item"].input_schema["required"]) == {"site", "number"}
    assert tools["search_guidelines"].input_schema["required"] == ["question"]
    assert tools["tidy_lab_files"].input_schema.get("required", []) == []
    assert tools["show_redactions"].input_schema.get("required", []) == []

    wrong = structured(await client.call_tool("check_paragraph", {"text": WALKTHROUGH_WRONG, "well": "MB2"}))
    statuses = {item["kind"]: item["status"] for item in wrong["checked"]}
    assert statuses == {"change": "inconsistent", "guideline": "depends_on_rule"}
    assert wrong["needs_attention"] == 2
    assert "no issues" not in str(wrong).lower()

    right = structured(await client.call_tool("check_paragraph", {"text": WALKTHROUGH_RIGHT, "well": "MB2"}))
    assert [item["status"] for item in right["checked"]] == ["consistent"]
    assert right["needs_attention"] == 0

    comparison = structured(await client.call_tool("compare_rules", {"well": "MB2", "date": "16 September 2025"}))
    by_rule = {rule["rule"]: rule for rule in comparison["rules"]}
    assert by_rule["nemp-3.0"]["overall"] == "not above"
    assert by_rule["current"]["overall"] == "above"
    assert by_rule["nemp-3.0"]["screens"][0]["arithmetic"] == "0.038 + 0.019 = 0.057"
    assert comparison["rules_agree"] is False

    limit = structured(await client.call_tool("lookup_limit", {"analyte": "PFOS", "rule": "current"}))
    assert (limit["value"], limit["unit"], limit["page"]) == ("0.008", "ug/L", "49")

    results = structured(await client.call_tool("get_results", {"well": "MB2", "analyte": "PFOS"}))
    assert [r["value"] for r in results["results"]] == ["0.062", "0.041", "0.038", "0.006"]
    assert {r["row"] for r in results["results"]} == {14}

    # Bad input comes back as a tool error the model can read, not a crash.
    assert "Unknown well 'MB7'" in error_text(await client.call_tool("get_results", {"well": "MB7"}))
    assert "Unknown rule 'x'" in error_text(await client.call_tool("lookup_limit", {"analyte": "PFOS", "rule": "x"}))
    assert "No MB2 round" in error_text(await client.call_tool("compare_rules", {"well": "MB2", "date": "Oct 2025"}))
    assert "empty" in error_text(await client.call_tool("check_paragraph", {"text": "", "well": "MB2"}))

    await _exercise_new_tools(client)


async def _exercise_new_tools(client: Client) -> None:
    tidy = structured(await client.call_tool("tidy_lab_files", {}))
    assert tidy["site"] == "FDS-01"
    assert "Synthetic data" in tidy["synthetic"]
    assert tidy["rows"] == []
    assert tidy["row_count"] == 73
    assert len(cast(list[object], tidy["review_items"])) == 6
    assert "no issues" not in str(tidy).lower()
    detail = structured(await client.call_tool("get_review_item", {"site": "FDS-01", "number": 3}))
    assert [line["row"] for line in detail["source_lines"]] == [15, 32, 40]
    assert "Unknown site 'X'" in error_text(await client.call_tool("tidy_lab_files", {"site": "X"}))
    assert "numbered 1 to 6" in error_text(await client.call_tool("get_review_item", {"site": "FDS-01", "number": 7}))

    # The drafting flow: placeholders in, exact values out, then the checker on the filled text.
    filled = structured(await client.call_tool("fill_numbers", {"text": TEMPLATE, "well": "MB2"}))
    assert "{" not in filled["text"]
    assert "0.038 ug/L" in filled["text"]
    assert "0.057 ug/L" in filled["text"]
    assert "a fall of 0.003 ug/L (7.3%)" in filled["text"]
    assert "check_paragraph" in filled["next_step"]
    checked = structured(await client.call_tool("check_paragraph", {"text": filled["text"], "well": "MB2"}))
    assert checked["checked"]
    assert {item["status"] for item in checked["checked"]} == {"consistent"}
    assert checked["needs_attention"] == 0
    assert [item["quote"] for item in checked["not_checked"]] == ["7.3"]
    assert "Nothing was filled" in error_text(
        await client.call_tool("fill_numbers", {"text": "{PFXX|MB2|Sep 2025}", "well": "MB2"})
    )

    question = {"question": "What must a detailed site investigation report include?", "k": 3}
    if default_index_path().exists():
        found = structured(await client.call_tool("search_guidelines", question))
        assert found["status"] == "passages found"
        assert 1 <= len(found["passages"]) <= 3
        assert all(passage["link"].startswith("https://") for passage in found["passages"])
        away = structured(
            await client.call_tool(
                "search_guidelines", {"question": "What does the NSW EPA sampling design guideline require?"}
            )
        )
        assert away["status"] == "not covered"
    else:
        assert "index" in error_text(await client.call_tool("search_guidelines", question)).lower()
    assert "error" in error_text(await client.call_tool("search_guidelines", {"question": "  "})).lower()

    # Clean synthetic data trips no redaction pattern: nothing above was rewritten.
    shown = structured(await client.call_tool("show_redactions", {}))
    assert shown["placeholders"] == []
    assert shown["replacements_by_type"] == {}


async def test_in_process_client() -> None:
    async with Client(server) as client:
        assert client.protocol_version == "2026-07-28"
        await _exercise(client)


def isolated_env() -> dict[str, str]:
    """The test's temporary home and audit log, for a subprocess (the SDK passes on only a short safe list)."""
    return {name: os.environ[name] for name in (redact.AUDIT_ENV, "HOME", "USERPROFILE") if name in os.environ}


def stdio_params(**env: str) -> StdioServerParameters:
    """``python -m evidenceline`` as a user would configure it, isolated from the real home folder."""
    return StdioServerParameters(
        command=sys.executable, args=["-m", "evidenceline"], cwd=str(REPO_ROOT), env=isolated_env() | env
    )


@pytest.mark.parametrize(("mode", "protocol"), [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
async def test_stdio_subprocess(mode: str, protocol: str) -> None:
    """The stateless 2026-07-28 protocol, and the older initialize handshake that existing clients still use."""
    params = stdio_params()
    async with Client(params, mode=mode, read_timeout_seconds=60) as client:
        assert client.protocol_version == protocol
        await _exercise(client)


async def test_console_script_over_stdio() -> None:
    script = shutil.which("evidenceline-mcp", path=str(Path(sys.executable).parent))
    if script is None:
        pytest.skip("evidenceline-mcp is not installed in this environment (run: pip install -e .)")
    async with Client(StdioServerParameters(command=script, env=isolated_env()), read_timeout_seconds=60) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS


async def test_input_schemas_reject_unknown_arguments() -> None:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        assert all(tool.input_schema.get("additionalProperties") is False for tool in tools)
        result = await client.call_tool("check_paragraph", {"text": "PFOS fell.", "well": "MB2", "wel": "MB2"})
        assert result.is_error


async def test_every_tool_rejects_unknown_arguments() -> None:
    minimal: dict[str, dict[str, Any]] = {
        "tidy_lab_files": {},
        "get_review_item": {"site": "FDS-01", "number": 1},
        "get_results": {"well": "MB2"},
        "lookup_limit": {"analyte": "PFOS", "rule": "current"},
        "compare_rules": {"well": "MB2", "date": "Sep 2025"},
        "check_paragraph": {"text": "PFOS fell.", "well": "MB2"},
        "fill_numbers": {"text": "{PFOS|MB2|Sep 2025}", "well": "MB2"},
        "search_guidelines": {"question": "soil reuse"},
        "show_redactions": {},
    }
    assert set(minimal) == EXPECTED_TOOLS
    async with Client(server) as client:
        for name, arguments in minimal.items():
            result = await client.call_tool(name, arguments | {"typo": "x"})
            assert "Extra inputs are not permitted" in error_text(result), name


async def test_unknown_tool_prompt_and_resource_names_are_never_repeated_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A name the server does not know comes from the caller and could hold a client name, so every reply is a fixed
    message and nothing from the request reaches the log (the SDK's own handlers repeat and log it)."""
    name = "Secret Client Pty Ltd"
    with caplog.at_level("DEBUG"):
        async with Client(server) as client:
            tool = await client.call_tool(name, {})
            with pytest.raises(MCPError) as prompt:
                await client.get_prompt(name)
            with pytest.raises(MCPError) as resource:
                await client.read_resource(f"file:///{name.replace(' ', '%20')}.txt")
    assert tool.is_error
    assert error_text(tool).startswith("Unknown tool. The tools are: ")
    assert prompt.value.error.message == UNKNOWN_PROMPT
    assert resource.value.error.message == UNKNOWN_RESOURCE
    assert resource.value.error.data is None
    replies = [error_text(tool), str(prompt.value), str(resource.value)]
    logged = [record.getMessage() + str(record.exc_info or "") for record in caplog.records]
    assert not any("Secret" in text for text in replies + logged)


async def test_descriptions_and_instructions_have_no_dashes() -> None:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    for text in [INSTRUCTIONS, *(str(tool.model_dump()) for tool in tools)]:
        assert not any(dash in text for dash in DASHES)
    for name in EXPECTED_TOOLS:
        assert name in INSTRUCTIONS
