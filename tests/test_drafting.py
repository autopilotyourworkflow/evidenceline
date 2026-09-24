"""fill_numbers: placeholders in, exact values and numbered sources out. No language model involved."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from evidenceline import core
from evidenceline.dataset import Dataset, load_dataset
from evidenceline.drafting import VALID_FORMS, FilledText, fill_placeholders, register_tools
from evidenceline.errors import EvidencelineError

from .conftest import write_lab_file


def fill(text: str, well: str = "MB2", data: Dataset | None = None) -> FilledText:
    return fill_placeholders(text, well, data=data)


def only(text: str, data: Dataset | None = None, well: str = "MB2") -> str:
    """The replacement for a text that is exactly one placeholder."""
    out = fill(text, well, data)
    assert len(out.values) == 1
    return out.text


def error(text: str, well: str = "MB2", data: Dataset | None = None) -> str:
    with pytest.raises(EvidencelineError) as info:
        fill(text, well, data)
    return str(info.value)


# --- each placeholder form -------------------------------------------------------------------------------------


def test_result_placeholder_gives_exact_value_and_lab_row() -> None:
    out = fill("PFOS was {PFOS|MB2|Sep 2025} in September 2025.")
    assert out.text == "PFOS was 0.038 ug/L in September 2025."
    value = out.values[0]
    assert (value.kind, value.value, value.text, value.placeholder) == (
        "result",
        "0.038",
        "0.038 ug/L",
        "{PFOS|MB2|Sep 2025}",
    )
    source = out.sources[value.source_numbers[0] - 1]
    assert (source.number, source.kind, source.file, source.rows, source.lab_report) == (
        1,
        "lab result",
        "mb2_round3_lab.csv",
        [14],
        "SYN-250916",
    )


@pytest.mark.parametrize(
    "date", ["Sep 2025", "September 2025", "sept 2025", "16 September 2025", "2025-09-16", "  sep 2025 "]
)
def test_every_date_form_resolves_to_the_same_round(date: str) -> None:
    assert only(f"{{PFOS|MB2|{date}}}") == "0.038 ug/L"


def test_fields_ignore_case_and_spaces() -> None:
    assert only("{ pfhxs | mb2 | 2024-11-19 }") == "0.018 ug/L"


def test_non_detect_is_written_as_the_lab_reported_it() -> None:
    out = fill("PFOA was not detected ({PFOA|MB2|Sep 2025}).")
    assert out.text == "PFOA was not detected (<0.001 ug/L)."
    value = out.values[0]
    assert value.value is None
    assert "not detected" in value.explanation
    assert out.sources[0].kind == "detection limit"
    assert out.sources[0].rows == [6]


def test_sum_is_computed_by_code_with_both_rows() -> None:
    out = fill("{sum PFOS+PFHxS|MB2|2025-09-16}")
    assert out.text == "0.057 ug/L"
    kinds = [s.kind for s in out.sources]
    assert kinds == ["computed sum", "lab result", "lab result"]
    assert out.sources[0].rows == [14, 15]
    assert "0.038 + PFHxS 0.019 = 0.057 ug/L" in out.sources[0].reference


@pytest.mark.parametrize("field", ["sum PFOS+PFHxS", "SUM pfhxs + pfos", "sum of PFOS and PFHxS", "PFOS+PFHxS"])
def test_sum_field_spellings(field: str) -> None:
    assert only(f"{{{field}|MB2|Mar 2024}}") == "0.083 ug/L"


def test_detection_limit_placeholder() -> None:
    out = fill("{lor|PFOA|MB2|Sep 2025}")
    assert out.text == "0.001 ug/L"
    assert out.values[0].kind == "detection limit"
    assert "not detected" in out.values[0].explanation


@pytest.mark.parametrize(
    ("placeholder", "expected", "page"),
    [
        ("{limit|PFOS|current}", "0.008 ug/L", "printed page 49"),
        ("{limit|PFHxS|current}", "0.03 ug/L", "printed page 49"),
        ("{limit|PFBS|current}", "1 ug/L", "printed page 49"),
        ("{limit|PFOA|current}", "0.2 ug/L", "printed page 49"),
        ("{limit|PFOS+PFHxS|nemp-3.0}", "0.07 ug/L", "PDF page 57"),
        ("{limit|sum PFOS+PFHxS|NEMP-3.0}", "0.07 ug/L", "PDF page 57"),
        ("{limit|PFOA|nemp-3.0}", "0.56 ug/L", "PDF page 57"),
    ],
)
def test_limit_placeholder_cites_table_and_page(placeholder: str, expected: str, page: str) -> None:
    out = fill(placeholder)
    assert out.text == expected
    source = out.sources[0]
    assert (source.kind, source.table, source.page) == ("guideline limit", "Table 4", page)
    assert out.values[0].rule in {"current", "nemp-3.0"}


def test_change_gives_direction_absolute_and_percent() -> None:
    out = fill("PFOS showed {change|PFOS|MB2|Nov 2024|Sep 2025} between November 2024 and September 2025.")
    assert out.text == "PFOS showed a fall of 0.003 ug/L (7.3%) between November 2024 and September 2025."
    value = out.values[0]
    assert (value.kind, value.value) == ("change", "-0.003")
    diff = out.sources[0]
    assert diff.kind == "computed difference"
    assert diff.value == "0.003"
    assert "= -0.003 ug/L (-7.3%)" in diff.reference
    assert [s.rows for s in out.sources[1:]] == [[14], [14]]
    assert {s.file for s in out.sources[1:]} == {"mb2_round2_lab.csv", "mb2_round3_lab.csv"}


def test_change_rise() -> None:
    out = fill("{change|PFHxS|MB2|Nov 2024|Sep 2025}")
    assert out.text == "a rise of 0.001 ug/L (5.6%)"
    assert out.values[0].value == "+0.001"


def test_change_of_the_sum() -> None:
    out = fill("{change|sum PFOS+PFHxS|MB2|Mar 2024|May 2026}")
    assert out.text == "a fall of 0.068 ug/L (81.9%)"
    assert [s.kind for s in out.sources].count("computed sum") == 2


def test_no_change(boundary_data: Dataset) -> None:
    out = fill("{change|PFOS|MB9|Jan 2025|Jun 2025}", "MB9", boundary_data)
    assert out.text == "no change (0.008 ug/L in both rounds)"
    assert out.values[0].value == "0"


# --- exact formatting --------------------------------------------------------------------------------------------


def _decimal_trap(tmp_path: Path) -> Dataset:
    """Values whose float sum is 0.30000000000000004, and a round reported in ng/L."""
    shutil.copy(Path(str(resources.files("evidenceline") / "data" / "guidelines.json")), tmp_path / "guidelines.json")
    write_lab_file(tmp_path, "mb9_round1_lab.csv", "2025-01-10", "SYN-1", [("PFOS", "0.1"), ("PFHxS", "0.2")])
    header = "lab_report_id,site_id,well_id,sample_id,sample_date,matrix,analyte,result,unit,lor\n"
    rows = [
        "SYN-2,FDS-01,MB9,MB9-20250610,2025-06-10,groundwater,PFOS,38,ng/L,1\n",
        "SYN-2,FDS-01,MB9,MB9-20250610,2025-06-10,groundwater,PFHxS,0.0190,ug/L,0.001\n",
    ]
    (tmp_path / "mb9_round2_lab.csv").write_text(header + "".join(rows), encoding="utf-8")
    return load_dataset(tmp_path)


def test_values_are_exact_decimals_without_float_artefacts(tmp_path: Path) -> None:
    ds = _decimal_trap(tmp_path)
    assert only("{sum PFOS+PFHxS|MB9|Jan 2025}", ds, "MB9") == "0.3 ug/L"
    assert only("{PFOS|MB9|Jun 2025}", ds, "MB9") == "0.038 ug/L"  # 38 ng/L
    assert only("{lor|PFOS|MB9|Jun 2025}", ds, "MB9") == "0.001 ug/L"  # 1 ng/L
    assert only("{PFHxS|MB9|Jun 2025}", ds, "MB9") == "0.019 ug/L"  # no trailing zero
    assert only("{change|PFOS|MB9|Jan 2025|Jun 2025}", ds, "MB9") == "a fall of 0.062 ug/L (62.0%)"


def test_boundary_values_keep_their_form(boundary_data: Dataset) -> None:
    assert only("{sum PFOS+PFHxS|MB9|Jan 2025}", boundary_data, "MB9") == "0.07 ug/L"


# --- the whole result ------------------------------------------------------------------------------------------

PARAGRAPH = (
    "PFOS was {PFOS|MB2|Sep 2025} in September 2025, above the current guideline of {limit|PFOS|current}. "
    "The sum of PFOS and PFHxS was {sum PFOS+PFHxS|MB2|Sep 2025} in September 2025, below the NEMP 3.0 guideline "
    "of {limit|PFOS+PFHxS|nemp-3.0}. PFOS showed {change|PFOS|MB2|Nov 2024|Sep 2025} between November 2024 and "
    "September 2025. PFOA was not detected in September 2025 ({PFOA|MB2|Sep 2025})."
)


def test_offsets_point_at_each_replacement() -> None:
    out = fill(PARAGRAPH)
    assert [v.number for v in out.values] == [1, 2, 3, 4, 5, 6]
    for value in out.values:
        assert out.text[value.start : value.end] == value.text
    assert "{" not in out.text
    assert "}" not in out.text


def test_sources_are_numbered_once_and_reused() -> None:
    out = fill(PARAGRAPH)
    assert [s.number for s in out.sources] == list(range(1, len(out.sources) + 1))
    assert len({(s.kind, s.reference) for s in out.sources}) == len(out.sources)
    pfos_sep = out.values[0].source_numbers[0]
    assert pfos_sep in out.values[2].source_numbers  # the sum reuses the PFOS row
    assert pfos_sep in out.values[4].source_numbers  # so does the change


def test_annotated_text_carries_source_numbers() -> None:
    out = fill("PFOS was {PFOS|MB2|Sep 2025}; the sum was {sum PFOS+PFHxS|MB2|Sep 2025}.")
    assert out.annotated_text == "PFOS was 0.038 ug/L [1]; the sum was 0.057 ug/L [2, 1, 3]."


def test_filled_paragraph_passes_the_checker() -> None:
    out = fill(PARAGRAPH)
    checked = core.check_paragraph(out.text, "MB2")
    assert checked.checked
    assert all(item.status == "consistent" for item in checked.checked)
    assert [n.quote for n in checked.not_checked] == ["7.3"]  # percentages are not traced, and the notes say so


def test_result_recommends_check_paragraph_and_keeps_the_guard_rails() -> None:
    out = fill(PARAGRAPH)
    assert "check_paragraph" in out.next_step
    notes = " ".join(out.notes)
    assert "synthetic" in notes
    assert "scientist's call" in notes
    assert "investigation level" in notes
    assert "does not trace percentages" in notes


def test_quoting_one_rule_only_points_to_the_other() -> None:
    out = fill("The current guideline is {limit|PFOS|current}.")
    assert any("Only the current guideline values are quoted" in n and "nemp-3.0" in n for n in out.notes)


def test_no_dashes_in_user_facing_text() -> None:
    out = fill(PARAGRAPH)
    blob = out.model_dump_json() + " ".join(VALID_FORMS)
    assert chr(0x2014) not in blob
    assert chr(0x2013) not in blob


# --- errors ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("{PFAS|MB2|Sep 2025}", "unknown analyte 'PFAS'"),
        ("{value|MB2|Sep 2025}", "unknown analyte 'value'"),
        ("{PFOS|MB2}", "has 3 fields"),
        ("{PFOS|MB2|Sep 2025|extra}", "has 3 fields"),
        ("{limit|PFOS}", "has 3 fields"),
        ("{limit|PFOS|}", "Evidenceline never picks one"),
        ("{limit|PFOS|wa}", "Unknown rule 'wa'"),
        ("{limit|PFOS|nemp-3.0}", "it applies to PFOS on its own too (Table 4, footnote a"),
        ("{limit|PFHxS|nemp-3.0}", "{limit|PFOS+PFHxS|nemp-3.0}"),
        ("{limit|PFOS+PFHxS|current}", "screens PFOS and PFHxS separately"),
        ("{limit|PFBS|nemp-3.0}", "has values for: sum of PFOS and PFHxS, PFOA"),
        ("{PFOS|MB2|Oct 2025}", "no MB2 round on 'Oct 2025'"),
        ("{PFOS|MB2|2025}", "could not read the date '2025'"),
        ("{PFOS|MB2|16/09/2025}", "could not read the date"),
        ("{PFOS|MB2|last round}", "could not read the date"),
        ("{PFOS|MB7|Sep 2025}", "Unknown well 'MB7'"),
        ("{sum PFOS+PFHxS|MB2}", "has 3 fields"),
        ("{lor|sum PFOS+PFHxS|MB2|Sep 2025}", "a sum has no detection limit"),
        ("{change|PFOS|MB2|Sep 2025|Nov 2024}", "Write the earlier round first"),
        ("{change|PFOS|MB2|Sep 2025|2025-09-16}", "the same round"),
        ("{change|PFOA|MB2|Nov 2024|Sep 2025}", "A non-detect has no single value"),
        ("{change|PFOS|MB2|Nov 2024}", "has 5 fields"),
        ("{}", "has 3 fields"),
        ("PFOS was {PFOS|MB2|Sep 2025 in September.", "unmatched '{'"),
        ("PFOS was PFOS|MB2|Sep 2025} in September.", "unmatched '}'"),
        ("{{PFOS|MB2|Sep 2025}}", "unmatched '{'"),
    ],
)
def test_bad_placeholders_fail_with_the_valid_forms(text: str, fragment: str) -> None:
    message = error(text)
    assert fragment in message
    assert message.startswith("Nothing was filled.")
    assert "Valid forms:" in message
    for form in VALID_FORMS:
        assert form in message


def test_every_problem_is_listed_and_nothing_is_filled() -> None:
    message = error("{PFOS|MB2|Sep 2025} and {PFAS|MB2|Sep 2025} and {limit|PFOS|nemp-3.0}")
    assert "placeholder 2 {PFAS|MB2|Sep 2025}" in message
    assert "placeholder 3 {limit|PFOS|nemp-3.0}" in message
    assert "placeholder 1" not in message


def test_ambiguous_month_asks_for_the_day(tmp_path: Path) -> None:
    shutil.copy(Path(str(resources.files("evidenceline") / "data" / "guidelines.json")), tmp_path / "guidelines.json")
    write_lab_file(tmp_path, "mb9_round1_lab.csv", "2025-06-02", "SYN-1", [("PFOS", "0.01")])
    write_lab_file(tmp_path, "mb9_round2_lab.csv", "2025-06-20", "SYN-2", [("PFOS", "0.02")])
    ds = load_dataset(tmp_path)
    assert "matches more than one round; give the day" in error("{PFOS|MB9|Jun 2025}", "MB9", ds)
    assert only("{PFOS|MB9|20 June 2025}", ds, "MB9") == "0.02 ug/L"


def test_unknown_well_in_a_placeholder() -> None:
    assert "Unknown well 'MB9'" in error("{PFOS|MB9|Sep 2025}")


def test_other_well_in_the_same_dataset(tmp_path: Path) -> None:
    shutil.copytree(Path(str(resources.files("evidenceline") / "data")), tmp_path, dirs_exist_ok=True)
    write_lab_file(tmp_path, "mb9_round1_lab.csv", "2025-01-10", "SYN-1", [("PFOS", "0.01")])
    ds = load_dataset(tmp_path)
    assert "this call is for well MB2, but the placeholder names MB9" in error("{PFOS|MB9|Jan 2025}", "MB2", ds)


def test_text_without_placeholders_or_empty() -> None:
    assert "No placeholders found" in error("PFOS was 0.038 ug/L.")
    assert "The text is empty" in error("   ")
    assert "Unknown well 'MB7'" in error("{PFOS|MB2|Sep 2025}", "MB7")


# --- MCP tool ----------------------------------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _server() -> MCPServer:
    server = MCPServer(name="drafting-test")
    register_tools(server)
    return server


@pytest.mark.anyio
async def test_tool_over_mcp() -> None:
    async with Client(_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"fill_numbers"}
        tool = tools["fill_numbers"]
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        description = tool.description or ""
        for fragment in ("{PFOS|MB2|Sep 2025}", "{limit|PFOS|current}", "{change|", "check_paragraph", "{lor|"):
            assert fragment in description
        assert set(tool.input_schema["required"]) == {"text", "well"}

        ok: CallToolResult = await client.call_tool("fill_numbers", {"text": "{PFOS|MB2|Sep 2025}", "well": "MB2"})
        assert not ok.is_error
        content = cast(dict[str, Any], ok.structured_content)
        assert content["text"] == "0.038 ug/L"

        bad = await client.call_tool("fill_numbers", {"text": "{PFAS|MB2|Sep 2025}", "well": "MB2"})
        assert bad.is_error
        block = bad.content[0]
        assert isinstance(block, TextContent)
        assert "Valid forms:" in block.text
