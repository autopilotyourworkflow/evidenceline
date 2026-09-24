"""Evidenceline MCP server (official MCP Python SDK v2, ``mcp.server.MCPServer``).

Run over stdio with the ``evidenceline-mcp`` console script or ``python -m evidenceline``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from pydantic import BaseModel, Field

from evidenceline import core, drafting, redact
from evidenceline.errors import EvidencelineError
from evidenceline.guidance import tools as guidance_tools
from evidenceline.models import LimitInfo, ParagraphCheck, RoundComparison, WellResults
from evidenceline.tidy import tools as tidy_tools
from evidenceline.tooling import GuardedServer, guarded_tool

INSTRUCTIONS = """\
Evidenceline helps with PFAS monitoring work at one fictional Western Australian site, FDS-01. Every lab value is
synthetic; every guideline value is real and cited to its document, table and page. Nothing here calls a language
model: the numbers, checks and searches are all done by code.

Tools, in the order a job usually needs them:
- tidy_lab_files, get_review_item: combine the site's lab results, chain of custody and field sheet into one table
  and list numbered review items for the scientist (sample ids, units, duplicates, holding times, blanks, LOR).
- get_results, lookup_limit, compare_rules: groundwater results for well MB2 over four rounds, one guideline value,
  and one round screened under both drinking-water rules side by side.
- search_guidelines: passages from the indexed public guidance, with document, edition, page and link.
- fill_numbers, then check_paragraph: write a report paragraph with placeholders such as {PFOS|MB2|Sep 2025}, let
  fill_numbers put in the exact values, then check the finished paragraph.
- show_redactions: which client and site identifiers were replaced with placeholders in this session.

Ground rules when you use these tools:
- Never type a number yourself. Copy every value exactly, with its unit, from a tool result, or write a placeholder
  and call fill_numbers.
- There are two drinking-water rules. "nemp-3.0" (PFAS NEMP 3.0, named as adopted on the WA government page)
  screens the SUM of PFOS and PFHxS, and PFOS alone and PFHxS alone, against 0.07 ug/L (Table 4, footnote a).
  "current" (ADWG values updated 2025, listed in PFAS NEMP 3.1) screens PFOS and PFHxS SEPARATELY, with no sum.
  Choosing the rule is the scientist's call: show both, never pick one.
- A result equal to a limit is not above it. A guideline value is an investigation level, not a finding that water
  is unsafe or a site is contaminated. Never write "contaminated", "safe" or "fails" as a conclusion.
- Never read a guideline value from a search_guidelines excerpt. Use lookup_limit, or send the person to the cited
  page.
- Placeholders such as [CLIENT-1] or [ADDRESS-1] stand for identifiers removed before the text reached you. Use them
  as they are; never guess what they stand for.
- After drafting any report text, run check_paragraph on it and show the person every flagged and not-checked item.
  Show every review item from tidy_lab_files. Never describe a result as "no issues"; say "not checked" rather than
  guess.
"""

WellArg = Annotated[str, Field(description="Monitoring well id, for example 'MB2'.")]


def get_results(
    well: WellArg,
    analyte: Annotated[
        str | None, Field(description="Optional analyte to filter by, for example 'PFOS'. Omit for all analytes.")
    ] = None,
) -> WellResults:
    """Return the lab results for one monitoring well, oldest round first.

    Each result has the sample date, analyte, the result exactly as the lab reported it, the exact value in ug/L
    (null when not detected), the detection limit, the lab report id, and the file and row it came from, so any
    number can be traced to its source. Use these values verbatim; do not round them.
    """
    try:
        return core.get_results(well, analyte)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


def lookup_limit(
    analyte: Annotated[
        str,
        Field(description="'PFOS', 'PFHxS', 'PFOA', 'PFBS', or 'PFOS+PFHxS' for the sum of PFOS and PFHxS."),
    ],
    rule: Annotated[
        str,
        Field(
            description="'nemp-3.0' (PFAS NEMP 3.0, March 2025, named as adopted on the WA government page) or "
            "'current' (ADWG values updated 2025, as listed in PFAS NEMP 3.1)."
        ),
    ],
) -> LimitInfo:
    """Look up the drinking-water guideline value for one analyte under one rule.

    Returns the value and unit, whether the rule sets it for one analyte or for the sum of PFOS and PFHxS
    (applies_to), what to compare with it for the analyte asked about (compared_quantity), the source document,
    table and page, the rule's WA adoption status, and a one-line note in plain English.
    Under 'nemp-3.0', 0.07 ug/L applies to PFOS alone, PFHxS alone and their sum (Table 4, footnote a): asking for
    either returns that value, with the footnote quoted.
    Under 'current', there is no sum value. Drinking water is the only scenario loaded.
    """
    try:
        return core.lookup_limit(analyte, rule)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


def compare_rules(
    well: WellArg,
    date: Annotated[
        str,
        Field(description="Sample date of the round: '2025-09-16', '16 September 2025' or 'Sep 2025'."),
    ],
) -> RoundComparison:
    """Screen one monitoring round under BOTH guideline rules side by side, with the arithmetic shown.

    For each rule, every limit is listed with the value compared (for a sum, for example
    '0.038 + 0.019 = 0.057'), the limit, and whether the result is above or not above it. A result equal to
    the limit is not above it. The tool never picks a rule: choosing one is the scientist's call. When the two
    rules disagree, the notes say so; the lab values are identical in both columns.
    """
    try:
        return core.compare_rules(well, date)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


def check_paragraph(
    text: Annotated[str, Field(description="The draft report paragraph to check, in plain English.")],
    well: WellArg,
) -> ParagraphCheck:
    """Check a draft report paragraph against the lab data and guideline values, without any language model.

    It finds (a) every number with a unit (ug/L, µg/L, ng/L, mg/L) and traces it to a lab row, a detection limit,
    a computed sum or difference, or a guideline table, or flags it as untraced; (b) claims of change between two
    dates (increased, fell, lower than, stable) and tests them against the data; (c) claims that a result is
    above, below or within the guideline, evaluated under each rule; (d) claims that an analyte was or was not
    detected. Negation such as 'did not increase' is handled.

    Every checked item has a status: consistent, inconsistent, untraced, depends_on_rule or needs_judgement.
    Anything it could not read confidently is listed under not_checked with the reason. Show the person every
    item that is not 'consistent' and every not_checked item; the person decides what to change.
    """
    try:
        return core.check_paragraph(text, well)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc


CORE_TOOL_SPECS: tuple[tuple[Callable[..., BaseModel], str], ...] = (
    (get_results, "Get lab results"),
    (lookup_limit, "Look up a guideline limit"),
    (compare_rules, "Compare both rules for one round"),
    (check_paragraph, "Check a report paragraph"),
)


def build_tools() -> list[Tool]:
    """Every Evidenceline tool: read-only, strict arguments, and redaction around each call.

    Two results are not redacted, only their arguments and errors: ``search_guidelines`` quotes public documents
    verbatim (the only user text in it is the question, already redacted on the way in), and ``show_redactions``
    holds placeholder names and pattern descriptions by construction (redacting it would rewrite its own examples,
    such as 'Lot 123', into placeholders). ``fill_numbers`` is redacted around its filled values, never through
    them, so 'Lot ' followed by '0.038 ug/L' is not read as a land lot.
    """
    specs = (*tidy_tools.TOOL_SPECS, *CORE_TOOL_SPECS)
    tools: list[Tool] = [guarded_tool(fn, title) for fn, title in specs]
    tools.extend(guarded_tool(fn, title, output_redactor=drafting.redact_filled) for fn, title in drafting.TOOL_SPECS)
    tools.append(guarded_tool(guidance_tools.search_guidelines, guidance_tools.TOOL_TITLE, redact_output=False))
    tools.extend(guarded_tool(fn, title, redact_output=False) for fn, title in redact.TOOL_SPECS)
    return tools


mcp = GuardedServer(
    name="evidenceline",
    title="Evidenceline",
    description="PFAS monitoring tools for one fictional WA site: every number traced to its lab row or guideline "
    "table.",
    instructions=INSTRUCTIONS,
    version="0.1.0",
    tools=build_tools(),
)


def main() -> None:
    """Console entry point: run the server over stdio."""
    mcp.run("stdio")


if __name__ == "__main__":
    main()
