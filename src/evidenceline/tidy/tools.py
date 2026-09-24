"""MCP tools for tidying lab results: ``tidy_lab_files`` and ``get_review_item``.

Add them to a server with :func:`register_tools`, or pass :data:`TOOL_SPECS` through a builder that makes tool
arguments strict (as ``server.py`` does for the other tools).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from evidenceline.errors import EvidencelineError
from evidenceline.tidy import engine
from evidenceline.tidy.models import ReviewItemDetail, TidyResult, TidyRow

SiteArg = Annotated[str, Field(description="Site id. Only the synthetic site 'FDS-01' is available.")]


class TidyLabFiles(TidyResult):
    """``tidy_lab_files`` as the tool returns it: the combined rows only when asked for, with their count always."""

    rows: list[TidyRow] = Field(
        description="The combined table, one row per lab result. Empty unless include_rows is true; row_count "
        "says how many rows there are."
    )
    row_count: int = Field(description="How many combined rows the site has, whether or not they are included.")
    rows_included: bool


def rows_line(count: int, included: bool) -> str:
    """The note that says where the rows are."""
    if included:
        return f"All {count} combined rows are included below."
    return (
        f"The {count} combined rows (one per lab result, with file and row numbers) are left out to keep this "
        "short. Call tidy_lab_files again with include_rows=true to get them; get_review_item shows the rows "
        "behind any one review item."
    )


def tidy_lab_files(
    site: SiteArg = "FDS-01",
    include_rows: Annotated[
        bool,
        Field(
            strict=True,
            description="Include the full combined table (73 rows for FDS-01, about 25 KB). Default false: "
            "review items, checks and counts only.",
        ),
    ] = False,
) -> TidyLabFiles:
    """Tidy one site's lab results, chain of custody and field sheet into one table, and list what needs review.

    Converts units exactly (ug/L for water, mg/kg for soil; ng/L, mg/L and the spellings ug/L, µg/L, μg/L are
    understood), reads every date day first, matches sample ids across the three files, and runs six QA checks:
    sample ids, units, field duplicate RPD, holding times to extraction, blank detections linked through the field
    sheet's equipment column, and LOR against investigation levels (soil HIL A; both drinking-water rules for
    water, side by side).

    Returns counts by matrix and analyte, numbered review items (what was found, the evidence rows, the rule and
    its source, and what the scientist decides), everything that was checked and not flagged, and what was not
    checked. The combined rows (one per lab result, with file and row numbers) are included only with
    include_rows=true; row_count always says how many there are. Show the person every review item and the checked
    list; never describe the result as "no issues". The tool never merges, rejects or corrects a result, and a guideline
    value is an investigation level, not a finding that a site is contaminated. The data is synthetic.
    """
    try:
        result = engine.tidy_lab_files(site)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc
    fields = {name: getattr(result, name) for name in TidyResult.model_fields}
    fields["rows"] = result.rows if include_rows else []
    fields["notes"] = [*result.notes, rows_line(len(result.rows), include_rows)]
    return TidyLabFiles(**fields, row_count=len(result.rows), rows_included=include_rows)


def get_review_item(
    site: SiteArg,
    number: Annotated[int, Field(strict=True, description="Review item number from tidy_lab_files, starting at 1.")],
) -> ReviewItemDetail:
    """Return one review item from tidy_lab_files in full, with every evidence row quoted exactly as it is in its file.

    Use it to show the person the lab, chain-of-custody and field-sheet lines behind a finding, with their row
    numbers, the rule and its source, and the judgement left to the scientist. Numbers come from tidy_lab_files
    for the same site.
    """
    try:
        return engine.get_review_item(site, number)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

TOOL_SPECS: tuple[tuple[Callable[..., BaseModel], str], ...] = (
    (tidy_lab_files, "Tidy lab results"),
    (get_review_item, "Get one review item"),
)
"""(function, title) pairs, for a server that builds its tool list itself (for example with strict arguments)."""


def register_tools(server: MCPServer) -> None:
    """Add this module's tools to ``server``."""
    for fn, title in TOOL_SPECS:
        server.add_tool(fn, title=title, annotations=READ_ONLY)
