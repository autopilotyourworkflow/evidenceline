"""How every Evidenceline MCP tool is built: read-only, strict arguments, and the redaction guard rail.

The server builds its tools here rather than with the SDK's ``add_tool``, for two reasons:

* **Strict arguments.** The SDK's argument model ignores unknown keys. :func:`forbid_extra_arguments` swaps in a
  subclass with ``extra="forbid"``, so a typo such as ``wel`` is an error and the published input schema says
  ``additionalProperties: false``.
* **Redaction at the tool boundary.** :class:`GuardedTool` passes the call's text arguments, its result and any
  error message through :mod:`evidenceline.redact` before anything reaches the model. Doing it here, around the
  SDK's own argument validation, also covers validation errors, which quote the input they rejected.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast, override

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError, UnexpectedToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    GetPromptResult,
    InputRequiredResult,
    TextContent,
    ToolAnnotations,
)
from pydantic import BaseModel, ConfigDict

from evidenceline import redact
from evidenceline.errors import EvidencelineError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    from mcp.server.mcpserver.context import Context
    from pydantic.networks import AnyUrl

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
"""Every Evidenceline tool only reads packaged data or the local index."""


class GuardedTool(Tool):
    """A tool whose text arguments, result and error messages go through the redaction guard rail.

    Arguments are redacted before the tool runs, so text the tool echoes back (a paragraph, a question) carries the
    same placeholders as everything else, and character offsets in the result match the text the model receives.
    With ``redact_output`` off, only the arguments and errors are redacted: used for a tool whose result quotes
    public documents verbatim, where the only user text in the result is an echo of an argument that was already
    redacted. If the redaction settings cannot be loaded, the tool fails closed with an error and returns nothing.
    Every call writes exactly one audit line. Every argument is redacted, whatever its type, and so are argument
    names: the SDK's validation error quotes a rejected argument, and it must quote the placeholder.
    """

    redact_output: bool = True
    output_redactor: Callable[[Any, Callable[[Any], Any]], Any] | None = None
    """Redacts the result in place of the generic walk, given the result and a function that redacts any value.
    For a result whose own content must stay untouched, such as the numbers ``fill_numbers`` puts into the text."""

    @override
    async def run(
        self,
        arguments: dict[str, Any],
        context: Context[Any, Any],
        convert_result: bool = False,
    ) -> Any:
        try:
            redactor = redact.session()
        except EvidencelineError as exc:
            raise ToolError(f"Error executing tool {self.name}: {exc} No output was returned.") from exc
        counts: Counter[str] = Counter()
        # Every argument, of any type, and every argument name: a rejected argument is quoted in the SDK's error.
        safe_arguments = redactor.redact_value(arguments, counts)
        try:
            result = await super().run(safe_arguments, context, convert_result=False)
        except UnexpectedToolError:
            redactor.apply(None, tool=self.name, counts=counts)
            raise  # its message is the SDK's generic one, with nothing from the call in it
        except ToolError as exc:
            raise ToolError(redactor.apply(str(exc), tool=self.name, counts=counts)) from exc
        if self.output_redactor is not None:
            result = self.output_redactor(result, lambda value: redactor.redact_value(value, counts))
            redactor.apply(None, tool=self.name, counts=counts)  # one audit line, with the counts gathered above
        elif self.redact_output:
            result = redactor.apply(result, tool=self.name, counts=counts)
        else:
            redactor.apply(None, tool=self.name, counts=counts)  # still one audit line, for the arguments
        return self.fn_metadata.convert_result(result) if convert_result else result


UNKNOWN_PROMPT = "Unknown prompt. This server has no prompts."
UNKNOWN_RESOURCE = "Unknown resource. This server has no resources."
UNREADABLE_RESOURCE = "The resource could not be read."


class GuardedServer(MCPServer[Any]):
    """An MCPServer that never repeats, or logs, a tool, prompt or resource name it does not know.

    The SDK answers a call to a missing tool with 'Unknown tool: <name>' before any tool (and so before the
    redaction guard rail) runs, and logs the name at INFO. It answers an unknown prompt with 'Unknown prompt:
    <name>' (logged with a traceback) and an unknown resource with its URI (logged at INFO and sent back as error
    data). Those names come from the caller, and could hold a client name, so each gets a fixed message here and
    nothing from the request is logged.
    """

    def _unknown_tool_message(self) -> str:
        known = ", ".join(tool.name for tool in self._tool_manager.list_tools())
        return f"Unknown tool. The tools are: {known}."

    @override
    async def _handle_call_tool(
        self, ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult | InputRequiredResult:
        if self._tool_manager.get_tool(params.name) is None:
            # Answered here, before the SDK's handler, which would log the name it was sent.
            return CallToolResult(content=[TextContent(type="text", text=self._unknown_tool_message())], is_error=True)
        return await super()._handle_call_tool(ctx, params)

    @override
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        if self._tool_manager.get_tool(name) is None:
            raise ToolError(self._unknown_tool_message())
        return await super().call_tool(name, arguments, context)

    @override
    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None, context: Context[Any, Any] | None = None
    ) -> GetPromptResult | InputRequiredResult:
        if self._prompt_manager.get_prompt(name) is None:
            # An MCPError goes back as it is, without the dispatcher's traceback log.
            raise MCPError(code=INVALID_PARAMS, message=UNKNOWN_PROMPT)
        return await super().get_prompt(name, arguments, context)

    @override
    async def read_resource(
        self, uri: AnyUrl | str, context: Context[Any, Any] | None = None
    ) -> Iterable[ReadResourceContents] | InputRequiredResult:
        try:
            return await super().read_resource(uri, context)
        except ResourceNotFoundError:
            raise MCPError(code=INVALID_PARAMS, message=UNKNOWN_RESOURCE) from None
        except ResourceError:
            raise MCPError(code=INTERNAL_ERROR, message=UNREADABLE_RESOURCE) from None


def forbid_extra_arguments[T: Tool](tool: T) -> T:
    """Return ``tool`` with an argument model that rejects unknown argument names."""
    loose = tool.fn_metadata.arg_model
    strict = cast(
        type[ArgModelBase],
        type(loose.__name__, (loose,), {"model_config": ConfigDict(extra="forbid"), "__module__": loose.__module__}),
    )
    metadata = tool.fn_metadata.model_copy(update={"arg_model": strict})
    return tool.model_copy(update={"fn_metadata": metadata, "parameters": strict.model_json_schema(by_alias=True)})


def guarded_tool(
    fn: Callable[..., BaseModel],
    title: str,
    *,
    redact_output: bool = True,
    output_redactor: Callable[[Any, Callable[[Any], Any]], Any] | None = None,
) -> GuardedTool:
    """Build a read-only, strict-argument tool from ``fn``, with the redaction guard rail around every call."""
    tool = cast(GuardedTool, GuardedTool.from_function(fn, title=title, annotations=READ_ONLY))
    update = {"redact_output": redact_output, "output_redactor": output_redactor}
    return forbid_extra_arguments(tool.model_copy(update=update))
