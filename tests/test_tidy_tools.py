"""The tidy MCP tools, registered on a server and called through the SDK's own client."""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from evidenceline.tidy.tools import TOOL_SPECS, register_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _server() -> MCPServer:
    server = MCPServer(name="tidy-test")
    register_tools(server)
    return server


def _structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    content: object = result.structured_content
    assert isinstance(content, dict)
    return cast(dict[str, Any], content)


def _error(result: CallToolResult) -> str:
    assert result.is_error
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_tool_specs() -> None:
    assert [(fn.__name__, title) for fn, title in TOOL_SPECS] == [
        ("tidy_lab_files", "Tidy lab results"),
        ("get_review_item", "Get one review item"),
    ]


async def test_tools_listed_with_schemas() -> None:
    async with Client(_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"tidy_lab_files", "get_review_item"}
    for tool in tools.values():
        assert tool.description is not None
        assert len(tool.description) > 80
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
    assert tools["tidy_lab_files"].input_schema.get("required", []) == []
    assert set(tools["tidy_lab_files"].input_schema["properties"]) == {"site", "include_rows"}
    assert set(tools["get_review_item"].input_schema["required"]) == {"site", "number"}


async def test_tidy_and_review_item_over_mcp() -> None:
    async with Client(_server()) as client:
        tidy = _structured(await client.call_tool("tidy_lab_files", {}))
        assert tidy["site"] == "FDS-01"
        items = cast(list[dict[str, Any]], tidy["review_items"])
        assert [item["check"] for item in items] == [
            "sample ids",
            "units",
            "field duplicates",
            "holding times",
            "blanks",
            "LOR against criteria",
        ]
        assert tidy["rows"] == []
        assert (tidy["row_count"], tidy["rows_included"]) == (73, False)
        assert "no issues" not in str(tidy).lower()

        detail = _structured(await client.call_tool("get_review_item", {"site": "FDS-01", "number": 3}))
        assert cast(dict[str, Any], detail["item"])["title"].startswith("Field duplicate pair SB4-0.3 and QC1")
        lines = cast(list[dict[str, Any]], detail["source_lines"])
        assert [line["row"] for line in lines] == [15, 32, 40]

        assert "Unknown site 'X'" in _error(await client.call_tool("tidy_lab_files", {"site": "X"}))
        assert "Items are numbered 1 to 6" in _error(
            await client.call_tool("get_review_item", {"site": "FDS-01", "number": 9})
        )


async def test_rows_left_out_by_default_and_included_on_request() -> None:
    """By default the tool leaves out the 73 combined rows (most of the output) and says how to get them."""
    async with Client(_server()) as client:
        short = _structured(await client.call_tool("tidy_lab_files", {"site": "FDS-01"}))
        full = _structured(await client.call_tool("tidy_lab_files", {"site": "FDS-01", "include_rows": True}))
        rejected = await client.call_tool("tidy_lab_files", {"include_rows": "yes"})
    assert short["rows"] == []
    assert short["notes"][-1].startswith("The 73 combined rows")
    assert "include_rows=true" in short["notes"][-1]
    assert len(str(short)) < len(str(full)) * 0.7
    assert len(cast(list[object], full["rows"])) == full["row_count"] == 73
    assert full["rows_included"] is True
    assert full["notes"][-1] == "All 73 combined rows are included below."
    # Everything except the rows and the last note is identical in both.
    same = {key for key in full if key not in {"rows", "rows_included", "notes"}}
    assert {key: short[key] for key in same} == {key: full[key] for key in same}
    assert short["notes"][:-1] == full["notes"][:-1]
    assert rejected.is_error


def test_rows_match_the_engine() -> None:
    from evidenceline.tidy import engine
    from evidenceline.tidy.tools import tidy_lab_files

    expected = engine.tidy_lab_files("FDS-01")
    full = tidy_lab_files("FDS-01", include_rows=True)
    assert full.rows == expected.rows
    assert full.review_items == expected.review_items
    assert tidy_lab_files("FDS-01").rows == []
    for text in (*tidy_lab_files("FDS-01").notes, *full.notes):
        assert "\u2013" not in text
        assert "\u2014" not in text
