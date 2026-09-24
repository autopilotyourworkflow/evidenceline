"""MCP wiring for the 'Ask the guidelines' search.

Two ways to add the tool to the Evidenceline server:

- ``strict_tools()`` returns ready-built :class:`Tool` objects whose arguments reject unknown names (the same
  ``extra="forbid"`` pattern as :func:`evidenceline.tooling.forbid_extra_arguments`). The Evidenceline server
  itself builds the tool with :func:`evidenceline.tooling.guarded_tool`, which adds the redaction guard rail.
- ``register_tools(server)`` adds the tool to an existing server through the SDK's public ``add_tool``. The SDK's
  argument model then ignores unknown argument names instead of rejecting them.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from evidenceline.errors import EvidencelineError
from evidenceline.guidance import search
from evidenceline.guidance.models import GuidanceSearch
from evidenceline.tooling import forbid_extra_arguments

TOOL_TITLE = "Ask the guidelines"
_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def search_guidelines(
    question: Annotated[
        str,
        Field(
            description="A question in plain English, for example 'What must a detailed site investigation report "
            "include?' or 'Can PFAS-contaminated soil be reused?'. At most 500 characters."
        ),
    ],
    k: Annotated[int, Field(strict=True, description="How many passages to return, 1 to 10. Default 5.")] = 5,
) -> GuidanceSearch:
    """Search the indexed public guidance (ASC NEPM Schedule B1, PFAS NEMP 3.0, DWER guidelines on assessing and on
    reporting and classifying contaminated sites, and the ADWG PFAS fact sheet) and return the passages that match.

    Each passage gives the document title, edition and date, its WA status, the PDF page and the printed page
    number, the nearest section heading and the headings above it, a short excerpt (shorter for documents whose
    licence allows only brief quotes, always with the notice) and the official link. At most one passage comes from
    any one page. When no passage covers enough of the question, the result is 'not covered' with the reason,
    instead of weak matches.

    Never take a guideline value from an excerpt: tables extract badly. For PFAS drinking-water values use
    lookup_limit; for any other value, read it on the cited page. The tool never says which document or edition
    applies to a site. PFAS NEMP 3.1 is not indexed (its host was unreachable when the index was built).
    """
    try:
        return search.search_guidelines(question, k)
    except EvidencelineError as exc:
        raise ToolError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise ToolError(
            f"The guidance index could not be read ({exc}). Rebuild it with 'python scripts/build_index.py'."
        ) from exc


def strict_tools() -> list[Tool]:
    """The guidance tools as read-only tools whose arguments reject unknown names."""
    return [forbid_extra_arguments(Tool.from_function(search_guidelines, title=TOOL_TITLE, annotations=_READ_ONLY))]


def register_tools(server: MCPServer) -> None:
    """Add the guidance tools to ``server`` (through the SDK's public ``add_tool``)."""
    server.add_tool(search_guidelines, title=TOOL_TITLE, annotations=_READ_ONLY)
