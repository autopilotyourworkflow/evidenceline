"""An independent tester's cases for phase 2: the tools over HTTP, tidy, search, the held-out evaluation, the guideline
verification summary and the pre-publish scan.

Written by someone who did not write the code under test. Real product failures are marked
``xfail(strict=True)`` with the severity first in the reason; a strict xfail turns into a failure the day the product is
fixed, so the mark has to be removed then. Search quality on new questions is recorded as data (see ``NEW_QUESTIONS``),
not as failures, except where a result is clearly broken.

The MCP cases talk to ``/mcp`` of the web API through the MCP SDK's own Streamable HTTP client (in memory through the
ASGI app, and once through a real uvicorn server), so they exercise the same code a hosted Claude connector would. No
network access, no model call, nothing published.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, cast

import anyio
import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from evidenceline import redact
from evidenceline.api.app import create_app
from evidenceline.api.settings import Settings
from evidenceline.guidance import evaluate
from evidenceline.guidance.search import default_index_path, search_guidelines
from evidenceline.tidy.tools import tidy_lab_files
from evidenceline.verification import VERIFICATION_DIR, summary_from_files

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "src" / "evidenceline" / "data"
DASHES = ("\u2013", "\u2014")
TOOLS = {
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

requires_index = pytest.mark.skipif(
    not default_index_path().exists(), reason="no guidance index: run scripts/fetch_corpus.py and build_index.py"
)
requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def _field_sheet_header(label: str) -> str:
    text = (resources.files("evidenceline") / "data" / "fds01_site" / "field_sheet.csv").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(f"# {label}: "):
            return line.removeprefix(f"# {label}: ").removesuffix(" (fictional)")
    raise AssertionError(f"no '{label}' line in the field sheet header")


CLIENT = _field_sheet_header("Client")
ADDRESS = _field_sheet_header("Site address")
FORBIDDEN = (CLIENT, ADDRESS, *CLIENT.split(), "Example Road", "Welshpool")


# ---------------------------------------------------------------------------------------------------------------------
# Helpers: the hosted app and an MCP client over Streamable HTTP
# ---------------------------------------------------------------------------------------------------------------------


def _app(settings: Settings | None = None) -> FastAPI:
    """The web API as it would run on the host with no model key (live answers off; /mcp unaffected)."""
    return create_app(settings or Settings(), client_factory=lambda: None, index_path=default_index_path())


def _over_http[T](body: Callable[[Client], Awaitable[T]], app: FastAPI | None = None) -> T:
    """Run ``body`` with an MCP client connected to ``/mcp`` of the app, in memory."""
    target = app if app is not None else _app()

    async def run() -> T:
        async with target.router.lifespan_context(target):
            transport = httpx2.ASGITransport(app=target)
            http = httpx2.AsyncClient(transport=transport, base_url="http://testserver", timeout=120)
            async with http, Client(streamable_http_client("http://testserver/mcp", http_client=http)) as client:
                return await body(client)

    return anyio.run(run)


def _text(result: CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def _structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)[:400]
    assert result.structured_content is not None
    return cast(dict[str, Any], result.structured_content)


async def _call_all(client: Client, calls: list[tuple[str, dict[str, Any]]]) -> list[CallToolResult]:
    return [await client.call_tool(name, arguments) for name, arguments in calls]


def _assert_no_identifiers(outputs: list[str]) -> None:
    for output in outputs:
        for fragment in FORBIDDEN:
            assert fragment.casefold() not in output.casefold(), (fragment, output[:300])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LocalServer:
    """The app on a real uvicorn server on a local port, for as long as the ``with`` block lasts."""

    def __init__(self, app: FastAPI) -> None:
        self.port = _free_port()
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _LocalServer:
        self._thread.start()
        deadline = time.monotonic() + 20
        while not self._server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


# ---------------------------------------------------------------------------------------------------------------------
# 1. The tools over HTTP: listing, strict arguments, unknown tools, oversized input
# ---------------------------------------------------------------------------------------------------------------------


def test_http_lists_nine_read_only_strict_tools_with_no_dashes() -> None:
    async def body(client: Client) -> None:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == TOOLS
        for tool in listed.tools:
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is True, tool.name
            assert tool.annotations.destructive_hint is False, tool.name
            assert tool.input_schema.get("additionalProperties") is False, tool.name
            words = json.dumps([tool.title, tool.description, tool.input_schema], ensure_ascii=False)
            assert not any(d in words for d in DASHES), tool.name

    _over_http(body)


def test_http_unknown_tool_named_after_the_client_is_not_echoed() -> None:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(f"export_{CLIENT.replace(' ', '_')}_report", {"site": ADDRESS})

    result = _over_http(body)
    assert result.is_error
    text = _text(result)
    assert "Unknown tool. The tools are:" in text
    _assert_no_identifiers([result.model_dump_json()])


STRICT_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("get_results", {"wel": "MB2"}, "Extra inputs are not permitted"),
    ("lookup_limit", {"analyte": "PFOS", "rule": "current", "rules": "nemp-3.0"}, "Extra inputs are not permitted"),
    ("tidy_lab_files", {"include_rows": "true"}, "valid boolean"),
    ("tidy_lab_files", {"include_rows": 1}, "valid boolean"),
    ("get_review_item", {"site": "FDS-01", "number": "1"}, "valid integer"),
    ("get_review_item", {"site": "FDS-01", "number": True}, "valid integer"),
    ("get_review_item", {"site": "FDS-01", "number": 7}, "Items are numbered 1 to 6"),
    ("get_review_item", {"site": "FDS-01", "number": 0}, "Items are numbered 1 to 6"),
    ("search_guidelines", {"question": "What is a conceptual site model?", "k": True}, "valid integer"),
    ("search_guidelines", {"question": "What is a conceptual site model?", "k": 11}, "1 to 10"),
    ("search_guidelines", {"question": "What is a conceptual site model?", "k": 0}, "1 to 10"),
    ("tidy_lab_files", {"site": "FDS-02"}, "Sites available: FDS-01"),
    ("check_paragraph", {"text": "", "well": "MB2"}, "The paragraph is empty"),
    ("fill_numbers", {"text": "  ", "well": "MB2"}, "The text is empty"),
    ("lookup_limit", {"analyte": "PFBS", "rule": "nemp-3.0"}, "no drinking-water value for PFBS"),
]


@pytest.mark.parametrize(
    ("tool", "arguments", "words"), STRICT_CASES, ids=[f"{t}-{i}" for i, (t, _, _) in enumerate(STRICT_CASES)]
)
def test_http_bad_arguments_are_refused_with_a_reason(tool: str, arguments: dict[str, Any], words: str) -> None:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(tool, arguments)

    result = _over_http(body)
    assert result.is_error
    assert words in _text(result)
    assert not any(d in _text(result) for d in DASHES)


@pytest.mark.parametrize("size", [501, 20_000])
def test_http_oversized_question_is_refused_without_echo(size: int) -> None:
    question = ("zqx " * size)[: size - 1] + "q"

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("search_guidelines", {"question": question})

    result = _over_http(body)
    assert result.is_error
    text = _text(result)
    assert f"The question is {size} characters long; the limit is 500" in text
    assert "zqx zqx zqx" not in text


BIG_TEXT = "PFOS in MB2 was 0.038 ug/L in September 2025. " * (600_000 // 47)
BIG_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "check_paragraph", "arguments": {"text": BIG_TEXT, "well": "MB2"}},
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-11-25"}


def test_http_big_paragraph_does_not_stall_the_service() -> None:
    """A 600 KB paragraph sent straight to /mcp (no proxy in front) is processed in a worker, so the health check,
    which Render uses to decide whether to restart the service, still answers promptly. The API now caps request
    bodies (see the next test), so the cap is raised here to keep exercising the processing path."""
    body, headers = BIG_BODY, MCP_HEADERS
    outcome: dict[str, int] = {}
    with _LocalServer(_app(Settings(max_body_bytes=2_000_000))) as server:

        def big() -> None:
            response = httpx2.post(f"{server.origin}/mcp", json=body, headers=headers, timeout=180)
            outcome["status"] = response.status_code

        worker = threading.Thread(target=big)
        worker.start()
        time.sleep(0.5)
        started = time.monotonic()
        health = httpx2.get(f"{server.origin}/api/health", timeout=60)
        waited = time.monotonic() - started
        worker.join(timeout=180)
    assert health.status_code == 200
    assert waited < 3, f"health check waited {waited:.1f} s behind one large tool call"
    assert outcome.get("status") == 200


def test_http_big_paragraph_is_refused_by_the_default_body_cap() -> None:
    """With the default settings the same 600 KB request is refused with 413 before the tool runs."""
    with _LocalServer(_app()) as server:
        response = httpx2.post(f"{server.origin}/mcp", json=BIG_BODY, headers=MCP_HEADERS, timeout=60)
        health = httpx2.get(f"{server.origin}/api/health", timeout=10)
    assert response.status_code == 413
    assert health.status_code == 200


def test_http_search_matches_the_direct_call() -> None:
    if not default_index_path().exists():
        pytest.skip("no guidance index")
    question = "What must a detailed site investigation report include?"

    async def body(client: Client) -> dict[str, Any]:
        return _structured(await client.call_tool("search_guidelines", {"question": question, "k": 5}))

    over_http = _over_http(body)
    direct = search_guidelines(question, 5).model_dump(mode="json")
    assert over_http == direct


# ---------------------------------------------------------------------------------------------------------------------
# 2. Redaction over HTTP
# ---------------------------------------------------------------------------------------------------------------------

PACKAGED_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("tidy_lab_files", {}),
    ("tidy_lab_files", {"include_rows": True}),
    *[("get_review_item", {"site": "FDS-01", "number": n}) for n in range(1, 7)],
    ("get_results", {"well": "MB2"}),
    ("compare_rules", {"well": "MB2", "date": "Sep 2025"}),
    ("lookup_limit", {"analyte": "PFOS", "rule": "nemp-3.0"}),
    ("fill_numbers", {"text": "PFOS was {PFOS|MB2|Sep 2025} in September 2025.", "well": "MB2"}),
    ("show_redactions", {}),
]


def test_http_packaged_data_never_carries_the_fictional_identifiers() -> None:
    """The hosted default (no identifier file): the field sheet names a fictional client and address in its header;
    no tool result, including the full row table and every review item's quoted evidence lines, may carry them."""

    async def body(client: Client) -> list[CallToolResult]:
        return await _call_all(client, PACKAGED_CALLS)

    results = _over_http(body)
    assert not any(r.is_error for r in results), [(_text(r)[:200]) for r in results if r.is_error]
    _assert_no_identifiers([r.model_dump_json() for r in results])


def test_http_hostile_echo_is_redacted_with_an_identifier_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With an identifier file listing the client (as a firm would run it), every attempt to make a tool echo the
    client or the address over HTTP, including as an argument name and in rejected arguments, comes back clean."""
    identifiers = tmp_path / "redact.toml"
    identifiers.write_text(f'[[client]]\nnames = ["{CLIENT}"]\n', encoding="utf-8")
    monkeypatch.setenv(redact.CONFIG_ENV, str(identifiers))
    redact.reset_session()
    paragraph = f"At the {CLIENT} site, {ADDRESS}, PFOS in MB2 was 0.038 ug/L in September 2025."
    calls: list[tuple[str, dict[str, Any]]] = [
        ("check_paragraph", {"text": paragraph, "well": "MB2"}),
        ("check_paragraph", {"text": paragraph.upper(), "well": "MB2"}),
        ("fill_numbers", {"text": f"{CLIENT}: PFOS was {{PFOS|MB2|Sep 2025}}.", "well": "MB2"}),
        ("get_results", {"well": f"{CLIENT} MB2"}),
        ("get_results", {"well": "MB2", CLIENT.replace(" ", "_"): 1}),
        ("lookup_limit", {"analyte": ADDRESS, "rule": CLIENT}),
        ("tidy_lab_files", {"site": ADDRESS}),
        ("get_review_item", {"site": CLIENT, "number": 2}),
        ("show_redactions", {}),
    ]
    if default_index_path().exists():
        calls.append(("search_guidelines", {"question": f"Reporting duties for {CLIENT} at {ADDRESS}"}))

    async def body(client: Client) -> list[CallToolResult]:
        return await _call_all(client, calls)

    results = _over_http(body)
    _assert_no_identifiers([r.model_dump_json() for r in results])
    assert "[CLIENT-1]" in results[0].model_dump_json()
    assert "[ADDRESS-1]" in results[0].model_dump_json()


def test_http_hosted_default_redacts_the_address_by_pattern() -> None:
    paragraph = f"At {ADDRESS}, PFOS in MB2 was 0.038 ug/L in September 2025."

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("check_paragraph", {"text": paragraph, "well": "MB2"})

    output = _over_http(body).model_dump_json()
    assert "Example Road" not in output
    assert "Welshpool" not in output
    assert "[ADDRESS-1]" in output


def test_hosted_connector_does_not_promise_client_placeholders_it_cannot_make() -> None:
    async def body(client: Client) -> tuple[str, str]:
        checked = await client.call_tool("check_paragraph", {"text": f"{CLIENT} asked about MB2.", "well": "MB2"})
        return client.instructions or "", checked.model_dump_json()

    instructions, output = _over_http(body)
    assert "hosted, read-only copy" in instructions  # the hosted note was read, so the check below is real
    assert not any(d in instructions for d in DASHES)
    promises = "[CLIENT-1]" in instructions and "built-in patterns" in instructions
    echoed = CLIENT in output
    assert not (promises and echoed), "the hosted note promises client placeholders, and the client name was echoed"


# ---------------------------------------------------------------------------------------------------------------------
# 3. The website's proxy in front of /mcp (web/functions/_lib/proxy.js), end to end against a real server
# ---------------------------------------------------------------------------------------------------------------------

PROXY_SCRIPT = """
import { handleProxy } from %(proxy)s;
const [origin, secret, ...ips] = process.argv.slice(2);
const env = { API_ORIGIN: origin };
if (secret !== '-') env.PROXY_SHARED_SECRET = secret;
const body = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} });
const out = [];
for (const ip of ips) {
  const request = new Request('https://evidenceline.example/mcp', {
    method: 'POST',
    body,
    headers: {
      'content-type': 'application/json',
      accept: 'application/json, text/event-stream',
      'mcp-protocol-version': '2025-11-25',
      'cf-connecting-ip': ip,
      cookie: 'session=should-not-pass',
    },
  });
  const response = await handleProxy(request, env, fetch);
  out.push({ status: response.status, body: await response.text() });
}
console.log(JSON.stringify(out));
"""


def _through_proxy(tmp_path: Path, origin: str, secret: str | None, ips: list[str]) -> list[dict[str, Any]]:
    proxy = (REPO / "web" / "functions" / "_lib" / "proxy.js").resolve().as_uri()
    script = tmp_path / "call_proxy.mjs"
    script.write_text(PROXY_SCRIPT % {"proxy": json.dumps(proxy)}, encoding="utf-8")
    node = shutil.which("node")
    assert node is not None
    run = subprocess.run(
        [node, str(script), origin, secret or "-", *ips], capture_output=True, text=True, timeout=60, check=False
    )
    assert run.returncode == 0, run.stderr
    return cast(list[dict[str, Any]], json.loads(run.stdout.strip().splitlines()[-1]))


@requires_node
def test_proxy_to_mcp_end_to_end_without_a_secret(tmp_path: Path) -> None:
    """Node runs the real proxy code against the API on uvicorn: /mcp tools/list works through it, and the per-IP
    limit follows the visitor's address that the proxy passes on (one request per hour each)."""
    settings = Settings(mcp_per_hour=1, client_ip_header="x-evidenceline-client-ip")
    with _LocalServer(_app(settings)) as server:
        replies = _through_proxy(tmp_path, server.origin, None, ["198.51.100.7", "198.51.100.8", "198.51.100.7"])
    assert [r["status"] for r in replies] == [200, 200, 429]
    assert all(name in replies[0]["body"] for name in TOOLS)


@requires_node
def test_proxy_to_mcp_end_to_end_with_the_shared_secret(tmp_path: Path) -> None:
    shared = "-".join(("shared", "by", "proxy", "and", "api", "0123456789"))
    settings = Settings(proxy_secret=shared, client_ip_header="x-evidenceline-client-ip")
    with _LocalServer(_app(settings)) as server:
        direct = httpx2.post(
            f"{server.origin}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"accept": "application/json, text/event-stream", "x-evidenceline-client-ip": "1.2.3.4"},
        )
        replies = _through_proxy(tmp_path, server.origin, shared, ["198.51.100.7"])
    assert direct.status_code == 403  # a direct caller without the secret is refused
    assert replies[0]["status"] == 200, replies[0]["body"][:300]


# ---------------------------------------------------------------------------------------------------------------------
# 4. tidy_lab_files: include_rows on and off
# ---------------------------------------------------------------------------------------------------------------------


def _tidy_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    async def body(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        off = _structured(await client.call_tool("tidy_lab_files", {}))
        on = _structured(await client.call_tool("tidy_lab_files", {"site": "FDS-01", "include_rows": True}))
        return off, on

    return _over_http(body)


def test_tidy_rows_off_and_on_lose_nothing() -> None:
    off, on = _tidy_pair()
    assert (off["rows"], off["row_count"], off["rows_included"]) == ([], 73, False)
    assert (len(on["rows"]), on["row_count"], on["rows_included"]) == (73, 73, True)
    assert "include_rows=true" in off["notes"][-1]
    assert on["notes"][-1] == "All 73 combined rows are included below."
    same = set(off) - {"rows", "rows_included", "notes"}
    for key in sorted(same):
        assert off[key] == on[key], key
    assert off["notes"][:-1] == on["notes"][:-1]
    assert len(json.dumps(off)) < len(json.dumps(on)) // 1.5


def test_tidy_every_review_item_points_at_rows_that_carry_it() -> None:
    _, on = _tidy_pair()
    rows = cast(list[dict[str, Any]], on["rows"])
    items = cast(list[dict[str, Any]], on["review_items"])
    assert [i["number"] for i in items] == [1, 2, 3, 4, 5, 6]
    for item in items:
        evidence = {int(e["row"]) for e in item["evidence"] if e["file"] == "lab_results.csv"}
        carrying = {int(r["lab_row"]) for r in rows if item["number"] in r["review_items"]}
        assert evidence == carrying, item["number"]
    assert len({int(r["lab_row"]) for r in rows}) == 73


def test_tidy_get_review_item_matches_the_list() -> None:
    async def body(client: Client) -> list[dict[str, Any]]:
        return [
            _structured(await client.call_tool("get_review_item", {"site": "FDS-01", "number": n})) for n in range(1, 7)
        ]

    details = _over_http(body)
    off, _ = _tidy_pair()
    for detail, listed in zip(details, cast(list[dict[str, Any]], off["review_items"]), strict=True):
        assert detail["item"] == listed
        assert detail["source_lines"], listed["number"]


_TO_CANONICAL = {
    "ng/L": ("ug/L", Decimal("0.001")),
    "ug/L": ("ug/L", Decimal(1)),
    "\u00b5g/L": ("ug/L", Decimal(1)),
    "\u03bcg/L": ("ug/L", Decimal(1)),
    "mg/L": ("ug/L", Decimal(1000)),
    "mg/kg": ("mg/kg", Decimal(1)),
    "ug/kg": ("mg/kg", Decimal("0.001")),
}


def test_tidy_row_values_equal_the_lab_file_exactly() -> None:
    """Every tidied value, recomputed here from the raw lab file line it cites, with this test's own unit table."""
    lines = (DATA / "fds01_site" / "lab_results.csv").read_text(encoding="utf-8").splitlines()
    header = next(csv.reader([lines[1]]))
    rows = cast(list[dict[str, Any]], tidy_lab_files(include_rows=True).model_dump(mode="json")["rows"])
    for row in rows:
        raw = dict(zip(header, next(csv.reader([lines[int(row["lab_row"]) - 1]])), strict=True))
        unit, factor = _TO_CANONICAL[raw["unit"]]
        lor_unit, lor_factor = _TO_CANONICAL[raw["lor_unit"]]
        assert row["unit"] == unit == lor_unit, row
        assert Decimal(row["lor"]) == Decimal(raw["lor"]) * lor_factor, row
        if raw["prefix"] == "<":
            assert row["value"] is None, row
            assert row["detected"] is False, row
        else:
            assert Decimal(row["value"]) == Decimal(raw["result"]) * factor, row
            assert "e" not in row["value"].lower(), row
        assert row["analyte"], row
        assert row["sample_id"], row


# ---------------------------------------------------------------------------------------------------------------------
# 5. Search quality on ten new practitioner questions (data), and two off-topic ones
# ---------------------------------------------------------------------------------------------------------------------

NEW_QUESTIONS: list[tuple[str, str, dict[str, set[int] | set[str]]]] = [
    (
        "n01",
        "To what depth do the soil health investigation levels apply on a residential site?",
        {"nepm-b1": {13, 86}},
    ),
    (
        "n02",
        "Which guidelines apply when contaminated groundwater discharges to a lake that people swim in?",
        {"dwer-amcs": {76}, "nepm-b1": {26}},
    ),
    ("n03", "What does a preliminary site investigation involve?", {"dwer-amcs": {30, 31}}),
    (
        "n04",
        "How should purged groundwater from monitoring wells be disposed of?",
        {"dwer-amcs": {47}, "nemp-3.0": {173}},
    ),
    ("n05", "Can PFAS-contaminated construction water be reused for dust suppression?", {"nemp-3.0": {127, 128}}),
    (
        "n06",
        "What design considerations apply to stockpiling and containing PFAS-contaminated soil on site?",
        {"nemp-3.0": {94, 95, 96, 97, 98, 99}},
    ),
    (
        "n07",
        "How should fish be collected when sampling biota for a PFAS human health risk assessment?",
        {"nemp-3.0": {229, 230}},
    ),
    ("n08", "What does it mean when DWER stops the clock on classifying a reported site?", {"dwer-irc": {38}}),
    (
        "n09",
        "When is a mandatory auditor's report required for a contaminated site?",
        {"dwer-irc": {25, 43, 57, 58, 59, 93}},
    ),
    (
        "n10",
        "What levels of PFOS have been found in Australian drinking water supplies?",
        {"adwg-pfas": {"Levels detected in Australian drinking water"}},
    ),
]
"""Written for this test from the documents' own text (pages checked in .cache/corpus/text), never tuned on. n04's
NEMP page 173 was added after a first run showed it genuinely answers the question (purged water is PFAS waste)."""

OFF_TOPIC = [
    ("z01", "What is a good recipe for a lemon tart?"),
    ("z02", "What are EPA Victoria's rules for reusing PFAS-contaminated soil?"),
]

BROKEN: dict[str, str] = {}
"""Questions with a clearly broken result, marked xfail. n03 ('What does a preliminary site investigation involve?')
was here: the rare word 'involves' pulled in the DSI page. Fixed by treating 'involve' and 'entail' as question
framing words (guidance/synonyms.py STOPWORDS)."""


def _first_hit(expected: dict[str, set[int] | set[str]], question: str) -> tuple[str, int | None, list[str]]:
    result = search_guidelines(question, 8)
    top = [f"{p.document_id} {p.pdf_page or p.section}" for p in result.passages]
    for passage in result.passages:
        wanted = expected.get(passage.document_id, set())
        page_hit = passage.pdf_page is not None and passage.pdf_page in wanted
        section = passage.section or ""
        section_hit = any(isinstance(w, str) and (section == w or section.startswith(f"{w} > ")) for w in wanted)
        if page_hit or section_hit:
            return result.status, passage.rank, top
    return result.status, None, top


def _params() -> Iterator[Any]:
    for qid, question, expected in NEW_QUESTIONS:
        marks = [pytest.mark.xfail(strict=True, reason=BROKEN[qid])] if qid in BROKEN else []
        yield pytest.param(qid, question, expected, id=qid, marks=marks)


@requires_index
@pytest.mark.parametrize(("qid", "question", "expected"), list(_params()))
def test_new_practitioner_question_is_answered_from_a_right_page(
    qid: str, question: str, expected: dict[str, set[int] | set[str]]
) -> None:
    """Clearly broken only: a plain in-scope question answered 'not covered', or no right page in the top eight.
    The rank of the first right page is data; see the module notes."""
    status, rank, top = _first_hit(expected, question)
    assert status == "passages found", (qid, status)
    assert rank is not None, (qid, top)


@requires_index
@pytest.mark.parametrize(("qid", "question"), OFF_TOPIC, ids=[q for q, _ in OFF_TOPIC])
def test_off_topic_question_is_not_covered(qid: str, question: str) -> None:
    result = search_guidelines(question, 8)
    assert result.status == "not covered", (qid, [p.location for p in result.passages])
    assert result.passages == []


# ---------------------------------------------------------------------------------------------------------------------
# 6. The held-out evaluation cannot silently read the golden set
# ---------------------------------------------------------------------------------------------------------------------


def _ids_and_questions(golden: evaluate.Golden) -> tuple[set[str], set[str]]:
    items = [*golden.in_scope, *golden.out_of_scope]
    return {i.id for i in items}, {i.question for i in items}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def test_heldout_and_golden_are_different_files_with_no_shared_questions() -> None:
    assert evaluate.HELDOUT_PATH.resolve() != evaluate.GOLDEN_PATH.resolve()
    assert evaluate.HELDOUT_PATH.read_bytes() != evaluate.GOLDEN_PATH.read_bytes()
    held_ids, held_q = _ids_and_questions(evaluate.load_heldout())
    gold_ids, gold_q = _ids_and_questions(evaluate.load_golden())
    assert len(held_ids) == 30
    assert len(gold_ids) == 50
    assert not held_ids & gold_ids
    assert not held_q & gold_q
    for h in held_q:
        for g in gold_q:
            a, b = _words(h), _words(g)
            assert len(a & b) / len(a | b) < 0.6, (h, g)


def test_heldout_flag_scores_exactly_the_heldout_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[evaluate.Golden] = []

    def fake(golden: evaluate.Golden, *_: object) -> evaluate.EvalReport:
        seen.append(golden)
        return evaluate.EvalReport()

    monkeypatch.setattr(evaluate, "evaluate", fake)
    assert evaluate.main(["--heldout"]) == 0
    held_ids, _ = _ids_and_questions(evaluate.load_heldout())
    gold_ids, _ = _ids_and_questions(evaluate.load_golden())
    ids, _ = _ids_and_questions(seen[0])
    assert ids == held_ids
    assert not ids & gold_ids
    assert {i.group for i in seen[0].in_scope} == {evaluate.HELDOUT_GROUP}
    assert evaluate.main(["--heldout", "--golden"]) == 2


def test_build_accuracy_labels_each_set_with_its_own_file() -> None:
    spec = importlib.util.spec_from_file_location("build_accuracy_t", REPO / "scripts" / "build_accuracy.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    sets = {str(s[0]): Path(str(s[3])) for s in cast(tuple[tuple[object, ...], ...], module.SEARCH_SETS)}
    assert sets == {
        "held-out-2": evaluate.HELDOUT2_PATH,
        "golden": evaluate.GOLDEN_PATH,
        "held-out": evaluate.HELDOUT_PATH,
    }


def test_loading_the_golden_file_as_heldout_fails_loudly() -> None:
    with pytest.raises((TypeError, KeyError, ValueError)):
        evaluate.load_heldout(evaluate.GOLDEN_PATH)


def test_heldout_flag_refuses_a_golden_format_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    impostor = tmp_path / "guidance_heldout.json"
    shutil.copy(evaluate.GOLDEN_PATH, impostor)
    monkeypatch.setattr(evaluate, "HELDOUT_PATH", impostor)

    def fake(*_: object) -> evaluate.EvalReport:
        return evaluate.EvalReport()

    monkeypatch.setattr(evaluate, "evaluate", fake)
    try:
        code = evaluate.main(["--heldout"])
    except (TypeError, KeyError, ValueError):
        return
    assert code != 0


# ---------------------------------------------------------------------------------------------------------------------
# 7. The guideline verification summary agrees with the data files, value by value
# ---------------------------------------------------------------------------------------------------------------------


def _file_rows() -> dict[str, dict[str, str]]:
    """Each value read straight from the two data files, without the verification module."""
    rows: dict[str, dict[str, str]] = {}
    guidelines = json.loads((DATA / "guidelines.json").read_text(encoding="utf-8"))
    for rule in guidelines["rules"]:
        for limit in rule["limits"]:
            rows[f"water:{rule['id']}:{limit['key']}"] = {
                "value": str(limit["value"]),
                "unit": str(limit["unit"]),
                "table": str(rule["table"]),
                "page": str(rule["page"]),
                "note": str(limit.get("note", "")),
            }
    soil = json.loads((DATA / "fds01_site" / "soil_criteria.json").read_text(encoding="utf-8"))
    for crit in soil["criteria"]:
        rows[f"soil:{crit['id']}"] = {
            "value": str(crit["value"]),
            "unit": str(crit["unit"]),
            "table": str(crit["table"]),
            "page": str(crit["page"]),
            "note": str(crit.get("note", "")),
        }
    return rows


def test_summary_agrees_with_the_data_files_value_by_value() -> None:
    files = _file_rows()
    written = json.loads((VERIFICATION_DIR / "summary.json").read_text(encoding="utf-8"))
    assert written == summary_from_files()
    rows = {str(r["id"]): r for r in written["values"]}
    assert set(rows) == set(files)
    for key, row in rows.items():
        want = files[key]
        assert (row["value"], row["unit"]) == (want["value"], want["unit"]), key
        assert want["table"] in row["source"], key
        assert want["page"] in row["source"], key
        assert row["both_confirm"] is True, key
        assert row["verified_value_matches_file"] is True, key
    assert written["counts"]["values"] == len(files) == 16
    for note in written["notes_not_confirmed"]:
        assert note["file_note"] == files[str(note["id"])]["note"], note["id"]


def _numbers(text: str) -> set[Decimal]:
    """Every number in the text, read as printed and also with a thousands space joined ('7 400' in the tables)."""
    joined = re.sub(r"(?<!\d )\b(\d{1,2}) (\d{3})\b", r"\1\2", text)
    return {Decimal(n) for source in (text, joined) for n in re.findall(r"\d+(?:\.\d+)?", source)}


def test_each_value_is_in_both_passes_source_quotes() -> None:
    """Not just the verdict: each pass's own quote from the source contains the number the data file holds, and each
    pass saw the value the file holds now."""
    files = _file_rows()
    pass_a = json.loads((VERIFICATION_DIR / "pass-a.json").read_text(encoding="utf-8"))
    pass_b = json.loads((VERIFICATION_DIR / "pass-b.json").read_text(encoding="utf-8"))
    a_quotes = {
        "water:" + r["id"].removeprefix("guidelines.json/").replace("/", ":")
        if r["id"].startswith("guidelines.json/")
        else "soil:" + r["id"].removeprefix("soil_criteria.json/"): r
        for r in pass_a["values"]
    }
    b_quotes = {str(r["id"]): r for r in pass_b["records"]}
    for key, want in files.items():
        for name, record in (("A", a_quotes[key]), ("B", b_quotes[key])):
            assert str(record["file_says"]["value"]) == want["value"], (name, key)
            quoted = " ".join(str(q) for q in cast(list[object], record["source_says"]))
            assert Decimal(want["value"]) in _numbers(quoted), (name, key, quoted[:300])


def test_lookup_limit_over_http_returns_exactly_the_file_values() -> None:
    files = _file_rows()
    wanted = {k: v for k, v in files.items() if k.startswith("water:")}

    async def body(client: Client) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key in wanted:
            _, rule, analyte = key.split(":")
            out[key] = _structured(await client.call_tool("lookup_limit", {"analyte": analyte, "rule": rule}))
        return out

    got = _over_http(body)
    for key, want in wanted.items():
        assert (got[key]["value"], got[key]["unit"], got[key]["page"], got[key]["table"]) == (
            want["value"],
            want["unit"],
            want["page"],
            want["table"],
        ), key


def test_nemp_sum_rule_is_not_described_as_having_no_single_member_value() -> None:
    async def body(client: Client) -> list[dict[str, Any]]:
        return [
            _structured(await client.call_tool("lookup_limit", {"analyte": analyte, "rule": "nemp-3.0"}))
            for analyte in ("PFOS", "PFHxS")
        ]

    for result in _over_http(body):
        assert "no separate value" not in json.dumps(result), result["analyte"]


# ---------------------------------------------------------------------------------------------------------------------
# 8. scripts/prepublish_check.py on a temporary copy of the repository
# ---------------------------------------------------------------------------------------------------------------------

SCAN = REPO / "scripts" / "prepublish_check.py"
PLANTED_KEY = "sk-ant-api03-" + "Zq7Xw2Vb9Nc4Md8Lf1Kg5Jh3Pr6St0Uy"
PLANTED_TOKEN = "".join(("Qx7vB2mK9p", "L4nR8tY1wE", "5zA3cD6fG0", "hJ2kLmNoPq"))
WINDOWS_USER_PATH = "\\".join(("C:", "Users", "someone", "Documents", "evidenceline"))
DATA_DRIVE_PATH = "/".join(("E:", "Work", "private", "folder"))
"""Planted values are put together at run time, so this file does not trip the scan it tests."""


def _scan(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCAN), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _scanner() -> Any:
    spec = importlib.util.spec_from_file_location("prepublish_check_t", SCAN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repo_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every file the scan would publish from the real checkout (the .gitignore files included), copied."""
    root = tmp_path_factory.mktemp("repo")
    for rel in _scanner().files_to_publish(REPO):
        target = root / Path(str(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / Path(str(rel)), target)
    return root


def test_prepublish_catches_planted_secret_path_and_dash(repo_copy: Path) -> None:
    planted = repo_copy / "docs" / "planted-notes.md"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(
        "# Notes\n\n"
        f"ANTHROPIC_API_KEY={PLANTED_KEY}\n"
        f"The files are in {WINDOWS_USER_PATH}\n"
        f"Also under {DATA_DRIVE_PATH}\n"
        "A sentence with an em dash \u2014 here.\n",
        encoding="utf-8",
    )
    (repo_copy / "config.env").write_text("PORT=8000\n", encoding="utf-8")
    ignored = repo_copy / ".deploy" / "cloudflare.env"
    ignored.parent.mkdir(parents=True, exist_ok=True)
    ignored.write_text(f"CLOUDFLARE_API_TOKEN={PLANTED_TOKEN}\n", encoding="utf-8")
    try:
        run = _scan(repo_copy)
    finally:
        planted.unlink()
        (repo_copy / "config.env").unlink()
        shutil.rmtree(repo_copy / ".deploy")
    assert run.returncode == 1
    mine = [line for line in run.stdout.splitlines() if line.startswith("docs/planted-notes.md")]
    rules = {re.sub(r".*?\[([a-z-]+)\].*", r"\1", line) for line in mine}
    assert rules == {"secret", "local-path", "dash"}, mine
    assert any(":3: [secret]" in line for line in mine)
    assert any(":4: [local-path]" in line for line in mine)
    assert any(":5: [local-path]" in line for line in mine)
    assert any(":6: [dash]" in line for line in mine)
    assert "config.env: [env-file]" in run.stdout
    assert PLANTED_KEY not in run.stdout
    assert PLANTED_KEY[10:] not in run.stdout
    assert ".deploy" not in run.stdout
    assert PLANTED_TOKEN not in run.stdout


def test_prepublish_clean_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Clean\n\nPFOS was 0.038 ug/L, reported to 3 decimal places.\n", "utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text('PATH = "C:/Program Files/x"\nNAME = "C:/path/to/evidenceline"\n', "utf-8")
    run = _scan(tmp_path)
    assert run.returncode == 0, run.stdout
    assert "Clean: 2 files scanned" in run.stdout


def test_prepublish_catches_a_bearer_token_in_a_deploy_note(tmp_path: Path) -> None:
    (tmp_path / "DEPLOY.md").write_text(
        f'curl -H "Authorization: Bearer {PLANTED_TOKEN}" https://api.cloudflare.com/client/v4/zones\n', "utf-8"
    )
    run = _scan(tmp_path)
    assert run.returncode == 1, run.stdout
    assert "[secret]" in run.stdout, run.stdout


def test_production_web_settings_are_published_and_pass_the_scan() -> None:
    module = _scanner()
    published = {str(p): p for p in module.files_to_publish(REPO)}
    assert "web/.env.production" in published
    assert not list(module.scan_file(REPO, published["web/.env.production"]))
