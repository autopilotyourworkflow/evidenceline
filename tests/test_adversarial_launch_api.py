"""Adversarial tests before launch: the web API, the hosted MCP connector and the hosted demo's redaction.

Written by an independent tester who did not write this code. A case marked ``xfail(strict=True)`` is a real product
failure; its reason starts with its severity:

- critical: leaks client data, passes a false number or claim, or shows a false claim on the site;
- major: wrong result, crash, dead link or broken state;
- minor: unclear output.

Each xfail stops being expected (and the run turns red) the moment the product is fixed, so the marker must then be
removed.

Areas:

1. Hosted demo redaction (``EVIDENCELINE_REDACT=builtin:fds01-demo``, as render.yaml sets it) over /mcp and
   /api/ask, with spellings the earlier suites did not try: possessives, other separators, file names, links, JSON
   escapes, nested arguments, and every error path that quotes its input.
2. Fail closed: a missing or broken custom identifier file, an empty setting and an unknown built-in name.
3. Cloudflare Turnstile on the server, on and off: missing, empty, bad, dummy, replayed and test-secret tokens. The
   Cloudflare siteverify endpoint is simulated with a mock transport; nothing leaves this computer.
4. The proxy secret gate: other paths, methods and near-miss secrets.
5. lookup_limit after the NEMP 3.0 footnote change (PFOS alone and PFHxS alone under nemp-3.0, the sum there, and no
   sum under current), over the hosted connector.
6. render.yaml against the settings the app reads.

Every identifier here is fictional: the made-up client and address in the header of
``src/evidenceline/data/fds01_site/field_sheet.csv``. The model is always a fake, the question box's search is a fake,
and no network call is made.
"""

from __future__ import annotations

import json
import re
import tomllib
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evidenceline import redact
from evidenceline import server as stdio_server
from evidenceline.answer import FakeClient
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.clients import DEFAULT_MODEL
from evidenceline.api import app as app_module
from evidenceline.api.app import create_app
from evidenceline.api.gate import NOT_THROUGH_PROXY
from evidenceline.api.settings import PROXY_SECRET_HEADER, Settings, from_env
from evidenceline.api.turnstile import CloudflareTurnstile
from evidenceline.guidance.models import GuidanceSearch
from evidenceline.guidance.search import default_index_path
from evidenceline.screening import FOOTNOTE_A

from .test_answer import GOOD, PASSAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER_YAML = REPO_ROOT / "render.yaml"
WRANGLER = REPO_ROOT / "web" / "wrangler.jsonc"
SETTINGS_PY = REPO_ROOT / "src" / "evidenceline" / "api" / "settings.py"
GUIDELINES = REPO_ROOT / "src" / "evidenceline" / "data" / "guidelines.json"
ANSWERS = REPO_ROOT / "web" / "public" / "data" / "answers.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SITE = "https://evidenceline.autopilotyourworkflow.com"
DEMO = "builtin:fds01-demo"
DASHES = ("\u2013", "\u2014")
NO_INDEX = Path("no-such-index.sqlite")
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
TOOL_NAMES = tuple(tool.name for tool in stdio_server.build_tools())
QUESTION = "What goes in a detailed site investigation report?"

# --- helpers -------------------------------------------------------------------------------------------------------

FORBIDDEN = ("harbourline", "exampleroad", "examplerd", "welshpool")
"""The fictional client and address, as letters only: a response is folded the same way before the check, so a name
that survives with any separator, escape, soft hyphen or URL encoding inside it is still caught."""


def _squash(text: str) -> str:
    """Letters only, case-folded, after undoing JSON escapes, URL encoding and HTML's soft hyphen entity."""
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\[ntr]", " ", text)
    text = urllib.parse.unquote(text).replace("&shy;", "")
    return re.sub(r"[^a-z]", "", text.casefold())


def _leaks(*outputs: str) -> list[str]:
    """Each forbidden fragment found, with a short piece of the output it was found in."""
    found: list[str] = []
    for output in outputs:
        squashed = _squash(output)
        found += [f"{fragment} in {output[:160]!r}" for fragment in FORBIDDEN if fragment in squashed]
    return found


def _assert_clean(*outputs: str) -> None:
    leaks = _leaks(*outputs)
    assert not leaks, leaks


@pytest.fixture(autouse=True)
def searches(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The question box's search is a fake that records what it was asked (the MCP tool keeps the real search)."""
    seen: list[str] = []

    def search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        del k, index_path
        seen.append(question)
        return GuidanceSearch.model_construct(
            question=question, status="passages found", explanation="2 passages.", passages=PASSAGES, notes=[]
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", search)
    return seen


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, DEMO)
    redact.reset_session()


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


def _app(
    settings: Settings | None = None,
    model: FakeClient | None = None,
    *,
    turnstile: Any = None,
) -> FastAPI:
    fixed = model if model is not None else FakeClient(reply=GOOD)
    return create_app(
        settings or Settings(),
        client_factory=lambda: fixed,
        clock=_Clock(),
        turnstile=turnstile,
        index_path=NO_INDEX,
    )


def _rpc(method: str, params: Any, id_: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


def _call(client: TestClient, name: str, arguments: Any, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """One tools/call over the hosted /mcp endpoint (stateless, JSON responses), as parsed JSON."""
    body = _rpc("tools/call", {"name": name, "arguments": arguments})
    response = client.post("/mcp", json=body, headers={**MCP_HEADERS, **(headers or {})})
    assert response.status_code == 200, response.text[:300]
    parsed: dict[str, Any] = response.json()
    return parsed


def _tool_text(reply: dict[str, Any]) -> str:
    result: dict[str, Any] = reply["result"]
    return "".join(str(part.get("text", "")) for part in result["content"])


def _structured(reply: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = reply["result"]
    assert not result.get("isError"), _tool_text(reply)
    structured: dict[str, Any] = result["structuredContent"]
    return structured


# ===================================================================================================================
# 1. Hosted demo redaction: new spellings of the fictional client and address
# ===================================================================================================================

SPELLINGS_REDACTED = [
    pytest.param("Harbourline's", id="possessive"),
    pytest.param("HARBOURLINE\u2019S", id="possessive, curly apostrophe, capitals"),
    pytest.param("Harbourline Logistics's", id="possessive of the full name"),
    pytest.param("Harbourline Logistics'", id="plural possessive"),
    pytest.param("Harbourline / Logistics", id="slash"),
    pytest.param("Harbourline -- Logistics", id="double hyphen"),
    pytest.param("Harbourline\r\nLogistics", id="CRLF"),
    pytest.param("Harbourline\tLogistics", id="tab"),
    pytest.param("Harbourline\u2003Logistics", id="em space"),
    pytest.param("Harbourline\u3000Logistics", id="ideographic space"),
    pytest.param("Harbour\u200bline", id="zero-width space inside the word"),
    pytest.param("Harbour\u2011Line Logistics", id="non-breaking hyphen"),
    pytest.param("HarbourlineLogisticsPtyLtd", id="all run together"),
    pytest.param("harbourline-logistics-dsi-2025.pdf", id="hyphenated file name"),
    pytest.param("www.harbourline.com.au", id="web address"),
    pytest.param("https://harbourline.com.au/sites/mb2", id="link"),
    pytest.param("info@harbourline.com.au", id="email at the client domain"),
    pytest.param("#harbourline", id="hashtag"),
    pytest.param("@HarbourlineLogistics", id="handle"),
    pytest.param("(Harbourline)", id="brackets"),
    pytest.param('"Harbourline"', id="double quotes"),
    pytest.param("Harbourline-owned", id="compound adjective"),
    pytest.param('{"client": "HARBOURLINE LOGISTICS", "site": "12 Example Rd, Welshpool"}', id="inside a JSON string"),
    pytest.param("12 EXAMPLE RD WELSHPOOL", id="address, capitals, no state"),
    pytest.param("12 Example Road,\nWelshpool WA 6106", id="address over two lines"),
    pytest.param("12 Example Road\tWelshpool", id="address with a tab"),
    pytest.param("12, Example Road", id="comma after the number"),
    pytest.param("Example Road's verge", id="street possessive"),
    pytest.param("ExampleRoad", id="street run together"),
    pytest.param("12_Example_Road_Welshpool.pdf", id="address as a file name"),
]

SPLIT_WORD = (
    "critical: the fictional client name leaks from the hosted demo when its one-word form is written as two words "
    "or with a hyphen or underscore inside ('Harbour Line', 'HARBOUR-LINE'). README promises matching 'in any "
    "letter case or spacing', and the demo file itself lists 'Harbour Line Logistics' as a spelling, but not "
    "'Harbour Line' on its own. Likely fix: src/evidenceline/data/fds01_site/redact.demo.toml (add 'Harbour Line'), "
    "or src/evidenceline/redact.py (_name_pattern: allow a separator inside a single listed word)."
)
GLUED = (
    "critical: the fictional client name leaks when it is joined to another word by an underscore or run into "
    "letters or digits, as in file names and codes ('Harbourline_Logistics_DSI_2025.pdf', 'DSI_Harbourline.pdf', "
    "'Harbourline2025', 'HarbourlineDSI'). The name pattern's boundaries (?<!\\w) and (?!\\w) count '_' and digits "
    "as part of a word. Likely fix: src/evidenceline/redact.py (Redactor: CONFIG group boundaries)."
)
URL_ENCODED = (
    "critical: the fictional client name leaks when a link carries it URL-encoded ('Harbour%20Line%20Logistics'): "
    "'%20' holds digits, so it is not a separator. Likely fix: src/evidenceline/redact.py (_JOINER: accept %20 and "
    "+), together with the 'Harbour Line' fix."
)

SPELLINGS_LEAKING = [
    pytest.param("Harbour Line", id="two words"),
    pytest.param("HARBOUR-LINE", id="hyphen inside"),
    pytest.param("harbour_line", id="underscore inside"),
    pytest.param("Harbourline_Logistics_DSI_2025.pdf", id="file name"),
    pytest.param("DSI_Harbourline.pdf", id="file name, name last"),
    pytest.param("Harbourline2025", id="year run on"),
    pytest.param("HarbourlineDSI", id="code run on"),
    pytest.param("https://example.com/clients/Harbour%20Line%20Logistics", id="URL-encoded link"),
]


def _mcp_outputs(client: TestClient, name: str) -> list[str]:
    """Every tool path a name can come back through: an echoed paragraph, filled text, argument errors, search."""
    replies = [
        _call(
            client,
            "check_paragraph",
            {"text": f"At {name}, PFOS in MB2 was 0.038 ug/L in September 2025.", "well": "MB2"},
        ),
        _call(client, "fill_numbers", {"text": f"{name}: PFOS was {{PFOS|MB2|Sep 2025}}.", "well": "MB2"}),
        _call(client, "lookup_limit", {"analyte": name, "rule": "current"}),
        _call(client, "compare_rules", {"well": "MB2", "date": f"{name} 2025"}),
        _call(client, "get_results", {"well": f"{name} MB2"}),
        _call(client, "search_guidelines", {"question": f"What goes in a DSI report for {name}?"}),
    ]
    return [json.dumps(reply, ensure_ascii=False) for reply in replies]


@pytest.mark.usefixtures("demo")
@pytest.mark.parametrize("name", [*SPELLINGS_REDACTED, *SPELLINGS_LEAKING])
def test_mcp_redacts_the_spelling(name: str) -> None:
    with TestClient(_app()) as client:
        outputs = _mcp_outputs(client, name)
    _assert_clean(*outputs)


@pytest.mark.usefixtures("demo")
@pytest.mark.parametrize("name", [*SPELLINGS_REDACTED, *SPELLINGS_LEAKING])
def test_ask_redacts_the_spelling(name: str, searches: list[str]) -> None:
    model = FakeClient(reply=GOOD)
    with TestClient(_app(model=model)) as client:
        response = client.post("/api/ask", json={"question": f"What goes in a DSI report for {name}?"})
    assert response.status_code == 200
    _assert_clean(response.text, *searches, *(prompt for _, prompt in model.calls))


PARTIAL_ADDRESS = (
    "minor: part of the fictional address is left beside the placeholder: 'Example Rd, WELSHPOOL' becomes "
    "'[ADDRESS-1], WELSHPOOL' and '12 Example Road Welshpool Western Australia 6106' becomes '[ADDRESS-1] 6106'. "
    "Only the suburb or postcode shows, not the client or street. Likely fix: redact.demo.toml (list 'Example Rd, "
    "Welshpool' and the long form with its postcode) or src/evidenceline/redact.py (extend a listed street to a "
    "following suburb, state and postcode)."
)


@pytest.mark.usefixtures("demo")
@pytest.mark.parametrize(
    "address",
    ["Example Rd, WELSHPOOL", "12 Example Road Welshpool Western Australia 6106"],
    ids=["abbreviated street then suburb", "long state name with postcode"],
)
def test_the_whole_listed_address_goes(address: str) -> None:
    redactor = redact.session()
    text = redactor.redact_text(f"Samples were taken at {address} in 2025.")
    assert "welshpool" not in text.casefold()
    assert "6106" not in text


@pytest.mark.usefixtures("demo")
def test_json_escapes_in_the_request_body_are_decoded_before_redaction(searches: list[str]) -> None:
    """A JSON body may spell letters as \\u escapes; the parser decodes them, and redaction must see the result."""
    raw_mcp = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_paragraph","arguments":'
        '{"text":"\\u0048arbourline \\u004cogistics at 12 \\u0045xample Road: PFOS in MB2 was 0.038 ug/L in '
        'September 2025.","well":"MB2"}}}'
    )
    raw_ask = '{"question": "\\u0048ARBOUR\\u00adLINE\\u00a0LOGISTICS: what goes in a DSI report?"}'
    model = FakeClient(reply=GOOD)
    with TestClient(_app(model=model)) as client:
        mcp = client.post("/mcp", content=raw_mcp.encode(), headers=MCP_HEADERS)
        ask = client.post("/api/ask", content=raw_ask.encode(), headers={"content-type": "application/json"})
    assert mcp.status_code == 200
    assert ask.status_code == 200
    assert "[CLIENT-1]" in mcp.text
    assert ask.json()["question"].startswith("[CLIENT-1]")
    _assert_clean(mcp.text, ask.text, *searches, *(prompt for _, prompt in model.calls))


@pytest.mark.usefixtures("demo")
def test_names_in_nested_and_wrongly_typed_arguments_are_redacted_in_the_error() -> None:
    """The SDK's validation error quotes a rejected argument with repr(): keys, lists and nested values."""
    with TestClient(_app()) as client:
        replies = [
            _call(
                client, "lookup_limit", {"analyte": {"Harbourline Logistics": ["12 Example Road"]}, "rule": "current"}
            ),
            _call(client, "lookup_limit", {"analyte": ["HARBOURLINE", "Harbour Line Logistics"], "rule": "current"}),
            _call(client, "check_paragraph", {"text": {"client": "Harbourline\nLogistics"}, "well": "MB2"}),
            _call(client, "get_results", {"well": "MB2", "analyte": 12, "Harbourline-Logistics": True}),
            _call(client, "compare_rules", {"well": "MB2", "date": "Sep 2025", "12 Example Rd": None}),
        ]
    texts = [json.dumps(reply, ensure_ascii=False) for reply in replies]
    assert all(reply["result"]["isError"] for reply in replies)
    _assert_clean(*texts)
    assert all("[CLIENT-1]" in text or "[ADDRESS-1]" in text for text in texts)


@pytest.mark.usefixtures("demo")
def test_ask_error_replies_never_echo_the_name(searches: list[str]) -> None:
    model = FakeClient(reply=GOOD)
    with TestClient(_app(Settings(ask_per_hour=1), model=model)) as client:
        too_long = client.post("/api/ask", json={"question": "Harbourline Logistics " + "q" * 600})
        extra = client.post("/api/ask", json={"question": "What is a CSM?", "Harbourline Logistics": "12 Example Rd"})
        wrong_type = client.post("/api/ask", json={"question": ["Harbourline Logistics"]})
        first = client.post("/api/ask", json={"question": "Harbourline Logistics: what is a CSM?"})
        limited = client.post("/api/ask", json={"question": "Harbourline Logistics: what is a CSM?"})
    assert [too_long.status_code, extra.status_code, wrong_type.status_code] == [400, 400, 400]
    assert first.status_code == 200
    assert limited.status_code == 429
    _assert_clean(too_long.text, extra.text, wrong_type.text, first.text, limited.text, *searches)


@pytest.mark.usefixtures("demo")
def test_unknown_prompt_and_resource_names_are_not_echoed_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("DEBUG"), TestClient(_app()) as client:
        prompt = client.post("/mcp", json=_rpc("prompts/get", {"name": "Harbourline Logistics"}), headers=MCP_HEADERS)
        resource = client.post(
            "/mcp", json=_rpc("resources/read", {"uri": "file:///Harbourline/12 Example Road.txt"}), headers=MCP_HEADERS
        )
        _call(client, "Harbourline Logistics", {})
    logged = [record.getMessage() + str(record.exc_info or "") for record in caplog.records]
    _assert_clean(prompt.text, resource.text, *logged)


@pytest.mark.usefixtures("demo")
def test_ordinary_tool_calls_never_put_the_name_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """Question text is never logged (app.py), and tool failures log only the redacted message."""
    with caplog.at_level("DEBUG"), TestClient(_app()) as client:
        _call(client, "lookup_limit", {"analyte": "Harbourline Logistics", "rule": "current"})
        _call(client, "check_paragraph", {"text": "12 Example Road: PFOS in MB2 was 0.038 ug/L.", "well": "MB2"})
        client.post("/api/ask", json={"question": "Harbourline Logistics at 12 Example Rd: what is a DSI?"})
    _assert_clean(*(record.getMessage() + str(record.exc_info or "") for record in caplog.records))


@pytest.mark.usefixtures("demo")
def test_placeholders_do_not_carry_raw_values_between_visitors() -> None:
    """Two visitors on the same hosted process: the second sees placeholders and counts, never the first's values."""
    with TestClient(_app()) as client:
        _call(
            client,
            "check_paragraph",
            {"text": "Jo at jo@firm.example.com.au, 0412 345 678, Harbourline.", "well": "MB2"},
        )
        summary = _tool_text(_call(client, "show_redactions", {}))
    assert "jo@firm" not in summary
    assert "0412" not in summary
    _assert_clean(summary)
    assert "[EMAIL-1]" in summary


# ===================================================================================================================
# 2. Fail closed: a broken or missing identifier file, an empty setting, an unknown built-in name
# ===================================================================================================================


def _broken_setting(kind: str, folder: Path) -> str:
    """The EVIDENCELINE_REDACT value for one broken case. File names hold the client name, so a path echo is caught."""
    folder = folder / "Harbourline Logistics"
    folder.mkdir()
    target = folder / "Harbourline Logistics.toml"
    contents: dict[str, bytes] = {
        "broken TOML": b'[[client]]\nnames = ["Harbourline Logistics\n',
        "unknown table": b'[[customer]]\nnames = ["Harbourline Logistics"]\n',
        "empty names": b"[[client]]\nnames = []\n",
        "one-letter name": b'[[client]]\nnames = ["H"]\n',
        "extra key": b'[[client]]\nnames = ["Harbourline Logistics"]\nalias = "HBL"\n',
        "names not a list": b'[[client]]\nnames = "Harbourline Logistics"\n',
        "not UTF-8": '[[client]]\nnames = ["Harbourline Logistics \u00e9"]\n'.encode("cp1252") + b"\xff\xfe",
    }
    if kind in contents:
        target.write_bytes(contents[kind])
        return str(target)
    return {
        "missing file": str(target),
        "a folder": str(folder),
        "empty setting": "",
        "whitespace setting": "   ",
        "unknown built-in": "builtin:harbourline-logistics",
        "empty built-in": "builtin:",
        "built-in path trick": "builtin:../fds01_site/redact.demo",
        "built-in with extension": "builtin:fds01-demo.toml",
    }[kind]


BROKEN = [
    "broken TOML",
    "unknown table",
    "empty names",
    "one-letter name",
    "extra key",
    "names not a list",
    "not UTF-8",
    "missing file",
    "a folder",
    "empty setting",
    "whitespace setting",
    "unknown built-in",
    "empty built-in",
    "built-in path trick",
    "built-in with extension",
]


@pytest.mark.parametrize("kind", BROKEN)
def test_every_tool_and_the_question_box_fail_closed(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, searches: list[str]
) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, _broken_setting(kind, tmp_path))
    redact.reset_session()
    model = FakeClient(reply=GOOD)
    with TestClient(_app(model=model)) as client:
        init = client.post(
            "/mcp",
            json=_rpc(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            ),
            headers=MCP_HEADERS,
        )
        replies = [_call(client, name, {"text": "Harbourline Logistics", "well": "MB2"}) for name in TOOL_NAMES]
        ask = client.post("/api/ask", json={"question": "Harbourline Logistics: what is a DSI?"})
        health = client.get("/api/health")
    for reply in replies:
        assert reply["result"]["isError"] is True, reply
        assert "structuredContent" not in reply["result"] or not reply["result"]["structuredContent"]
    assert ask.status_code == 503
    assert "redaction settings could not be loaded" in ask.json()["error"]
    assert model.calls == []
    assert searches == []
    assert health.status_code == 200
    assert health.json()["redaction"] == "The identifier file could not be loaded."
    instructions: str = init.json()["result"]["instructions"]
    assert "could not be read" in instructions
    assert "become placeholders" not in instructions
    _assert_clean(ask.text, health.text, instructions, *(json.dumps(reply) for reply in replies))


@pytest.mark.parametrize(
    "value",
    ["BUILTIN:FDS01-DEMO", "  builtin:fds01-demo  ", "builtin: fds01-demo", "Builtin:Fds01-Demo"],
    ids=["capitals", "padded", "space after the colon", "mixed case"],
)
def test_harmless_variants_of_the_builtin_setting_load_it(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, value)
    redact.reset_session()
    with TestClient(_app()) as client:
        health = client.get("/api/health").json()
        reply = _call(client, "check_paragraph", {"text": "Harbourline: PFOS in MB2 was 0.038 ug/L.", "well": "MB2"})
    assert health["redaction"] == "Built-in identifier file loaded (builtin:fds01-demo)."
    _assert_clean(json.dumps(reply))


def test_a_custom_identifier_file_is_honoured_on_both_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, searches: list[str]
) -> None:
    """A firm hosting its own copy with its own file: its names are redacted on /mcp and /api/ask."""
    path = tmp_path / "ids.toml"
    path.write_text('[[client]]\nnames = ["Quokka Minerals Pty Ltd", "Quokka"]\n', encoding="utf-8")
    monkeypatch.setenv(redact.CONFIG_ENV, str(path))
    redact.reset_session()
    model = FakeClient(reply=GOOD)
    with TestClient(_app(model=model)) as client:
        reply = _call(client, "check_paragraph", {"text": "QUOKKA's bore MB2 had PFOS at 0.038 ug/L.", "well": "MB2"})
        ask = client.post("/api/ask", json={"question": "Is quokka-minerals' DSI complete?"})
    text = json.dumps(reply) + ask.text + "".join(searches) + "".join(p for _, p in model.calls)
    assert "quokka" not in text.casefold()
    assert "[CLIENT-1]" in json.dumps(reply)


EMPTY_FILE = (
    "minor: with an identifier file that loads but lists nothing, the hosted instructions and show_redactions say "
    "'No identifier file is loaded' while /api/health says 'Identifier file loaded from the path in "
    "EVIDENCELINE_REDACT.'. The behaviour (no names redacted) is right; the words contradict each other. Likely fix: "
    "src/evidenceline/api/app.py (hosted_note) and src/evidenceline/redact.py (_explanation): say the file lists no "
    "names."
)


def test_an_empty_identifier_file_is_described_consistently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ids.toml"
    path.write_text("# nothing listed yet\n", encoding="utf-8")
    monkeypatch.setenv(redact.CONFIG_ENV, str(path))
    redact.reset_session()
    with TestClient(_app()) as client:
        health = client.get("/api/health").json()
        init = client.post(
            "/mcp",
            json=_rpc(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            ),
            headers=MCP_HEADERS,
        ).json()
        summary = _tool_text(_call(client, "show_redactions", {}))
    assert health["redaction"].startswith("Identifier file loaded")
    assert "No identifier file is loaded" not in init["result"]["instructions"]
    assert "No identifier file is loaded" not in summary


# ===================================================================================================================
# 3. Turnstile on the server, on and off
# ===================================================================================================================

CF_ALWAYS_PASSES = "1x0000000000000000000000000000000AA"
"""Cloudflare's documented test secret that always passes."""
CF_ALWAYS_FAILS = "2x0000000000000000000000000000000AA"
"""Cloudflare's documented test secret that always fails."""
CF_ALREADY_SPENT = "3x0000000000000000000000000000000AA"
"""Cloudflare's documented test secret that answers 'token already spent'."""
DUMMY_WIDGET_ANSWER = "XXXX.DUMMY.TOKEN.XXXX"
"""The token Cloudflare's test site keys hand out; a production secret rejects it."""
LIVE_TURNSTILE_KEY = "0x4AAAAAAA-not-a-real-secret-for-tests"
WIDGET_ANSWER = "0.good-token-from-the-widget"


class Siteverify:
    """A stand-in for Cloudflare's siteverify endpoint, following its documented answers. Records every form."""

    def __init__(self) -> None:
        self.forms: list[dict[str, str]] = []
        self.spent: set[str] = set()

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        form = dict(urllib.parse.parse_qsl(request.read().decode()))
        self.forms.append(form)
        secret, token = form.get("secret", ""), form.get("response", "")
        if secret == CF_ALWAYS_PASSES:
            return self._ok()
        if secret == CF_ALWAYS_FAILS:
            return self._no("invalid-input-response")
        if secret == CF_ALREADY_SPENT:
            return self._no("timeout-or-duplicate")
        if secret != LIVE_TURNSTILE_KEY:
            return self._no("invalid-input-secret")
        if not token.startswith("0.") or token == DUMMY_WIDGET_ANSWER:
            return self._no("invalid-input-response")
        if token in self.spent:
            return self._no("timeout-or-duplicate")
        self.spent.add(token)
        return self._ok()

    @staticmethod
    def _ok() -> httpx2.Response:
        return httpx2.Response(200, json={"success": True, "hostname": "evidenceline.autopilotyourworkflow.com"})

    @staticmethod
    def _no(code: str) -> httpx2.Response:
        return httpx2.Response(200, json={"success": False, "error-codes": [code]})


@pytest.fixture
def siteverify(monkeypatch: pytest.MonkeyPatch) -> Siteverify:
    """Turnstile built from the environment as in production, but talking to the stand-in, never to Cloudflare."""
    fake = Siteverify()

    def build(secret: str) -> CloudflareTurnstile:
        return CloudflareTurnstile(secret, transport=httpx2.MockTransport(fake.handle))

    monkeypatch.setattr(app_module, "CloudflareTurnstile", build)
    return fake


def _ask(client: TestClient, headers: dict[str, str] | None = None, **fields: Any) -> Any:
    return client.post("/api/ask", json={"question": QUESTION, **fields}, headers=headers or {})


def test_turnstile_off_ignores_the_token_and_never_calls_cloudflare(siteverify: Siteverify) -> None:
    settings = from_env({"TURNSTILE_SECRET": "   "})
    assert settings.turnstile_secret is None
    with TestClient(_app(settings)) as client:
        assert client.get("/api/health").json()["turnstile"] is False
        codes = [
            _ask(client).status_code,
            _ask(client, turnstile_token=None).status_code,
            _ask(client, turnstile_token="").status_code,
            _ask(client, turnstile_token=DUMMY_WIDGET_ANSWER).status_code,
        ]
    assert codes == [200, 200, 200, 200]
    assert siteverify.forms == []


def test_turnstile_on_from_the_environment(siteverify: Siteverify, searches: list[str]) -> None:
    model = FakeClient(reply=GOOD)
    with TestClient(_app(from_env({"TURNSTILE_SECRET": f" {LIVE_TURNSTILE_KEY} "}), model)) as client:
        assert client.get("/api/health").json()["turnstile"] is True
        refused = {
            "missing": _ask(client),
            "null": _ask(client, turnstile_token=None),
            "empty": _ask(client, turnstile_token=""),
            "dummy test token": _ask(client, turnstile_token=DUMMY_WIDGET_ANSWER),
            "made up": _ask(client, turnstile_token="0" * 40),
            "client name as token": _ask(client, turnstile_token="Harbourline Logistics"),
        }
        good = _ask(client, turnstile_token=WIDGET_ANSWER)
        replayed = _ask(client, turnstile_token=WIDGET_ANSWER)
    for label, response in refused.items():
        assert response.status_code == 403, label
        assert "not a robot" in response.json()["error"], label
        assert not any(dash in response.text for dash in DASHES), label
    assert good.status_code == 200
    assert good.json()["status"] == "answered"
    assert replayed.status_code == 403
    assert len(model.calls) == 1
    assert len(searches) == 1
    assert all(form["secret"] == LIVE_TURNSTILE_KEY for form in siteverify.forms)
    assert "Harbourline" not in json.dumps([r.text for r in refused.values()])


@pytest.mark.parametrize(
    ("secret", "code"),
    [(CF_ALWAYS_PASSES, 200), (CF_ALWAYS_FAILS, 403), (CF_ALREADY_SPENT, 403)],
    ids=["always passes", "always fails", "already spent"],
)
def test_cloudflare_test_secrets_behave_as_documented(siteverify: Siteverify, secret: str, code: int) -> None:
    with TestClient(_app(from_env({"TURNSTILE_SECRET": secret}))) as client:
        assert _ask(client, turnstile_token=DUMMY_WIDGET_ANSWER).status_code == code


NOT_AN_OBJECT = (
    "major: when siteverify answers with JSON that is not an object (a list, null or a string), the verifier raises "
    "AttributeError and /api/ask answers 500 instead of the 403 'did not pass'. It still fails closed (no answer), "
    "and Cloudflare itself always sends an object, so this needs a broken network path to happen; but the module "
    "promises any failure counts as 'not verified'. Likely fix: src/evidenceline/api/turnstile.py (check the payload "
    "is a dict before .get)."
)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"success": "true"}, id="string true"),
        pytest.param({"success": 1}, id="number 1"),
        pytest.param({}, id="no success key"),
        pytest.param({"success": None}, id="success null"),
        pytest.param([True], id="a list"),
        pytest.param(None, id="JSON null"),
        pytest.param("success", id="a JSON string"),
    ],
)
def test_anything_but_success_true_is_refused(payload: Any) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})

    gate = CloudflareTurnstile(LIVE_TURNSTILE_KEY, transport=httpx2.MockTransport(handler))
    with TestClient(_app(turnstile=gate)) as client:
        assert _ask(client, turnstile_token=WIDGET_ANSWER).status_code == 403


def test_siteverify_gets_the_address_the_proxy_vouches_for(siteverify: Siteverify) -> None:
    secret = "p" * 40
    trusted = from_env(
        {
            "TURNSTILE_SECRET": LIVE_TURNSTILE_KEY,
            "EVIDENCELINE_CLIENT_IP_HEADER": "x-evidenceline-client-ip",
            "EVIDENCELINE_PROXY_SECRET": secret,
        }
    )
    untrusted = from_env(
        {"TURNSTILE_SECRET": LIVE_TURNSTILE_KEY, "EVIDENCELINE_CLIENT_IP_HEADER": "x-evidenceline-client-ip"}
    )
    ip = {"x-evidenceline-client-ip": "198.51.100.7"}
    with TestClient(_app(trusted)) as client:
        assert _ask(client, {**ip, PROXY_SECRET_HEADER: secret}, turnstile_token="0.a").status_code == 200
    with TestClient(_app(untrusted)) as client:
        assert _ask(client, ip, turnstile_token="0.b").status_code == 200
    assert siteverify.forms[0]["remoteip"] == "198.51.100.7"
    assert siteverify.forms[1]["remoteip"] != "198.51.100.7"  # a header a direct caller typed is not passed on


def test_cloudflare_is_not_called_for_requests_refused_earlier(
    siteverify: Siteverify, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = from_env({"TURNSTILE_SECRET": LIVE_TURNSTILE_KEY, "EVIDENCELINE_PROXY_SECRET": "p" * 40})
    with TestClient(_app(settings)) as client:
        assert _ask(client, turnstile_token=WIDGET_ANSWER).status_code == 403  # no proxy secret
        auth = {PROXY_SECRET_HEADER: "p" * 40}
        assert (
            client.post("/api/ask", json={"question": "  ", "turnstile_token": WIDGET_ANSWER}, headers=auth).status_code
            == 400
        )
        long_question = {"question": "q" * 501, "turnstile_token": WIDGET_ANSWER}
        assert client.post("/api/ask", json=long_question, headers=auth).status_code == 400
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "missing.toml"))
    with TestClient(_app(from_env({"TURNSTILE_SECRET": LIVE_TURNSTILE_KEY}))) as client:
        assert _ask(client, turnstile_token=WIDGET_ANSWER).status_code == 503
    assert siteverify.forms == []


def test_the_mcp_connector_needs_no_turnstile_token(siteverify: Siteverify) -> None:
    """Turnstile guards the question box only: the connector is called by Claude, which has no widget."""
    with TestClient(_app(from_env({"TURNSTILE_SECRET": LIVE_TURNSTILE_KEY}))) as client:
        reply = _call(client, "lookup_limit", {"analyte": "PFOS", "rule": "current"})
    assert _structured(reply)["value"] == "0.008"
    assert siteverify.forms == []


BAD_TOKENS_UNLIMITED = (
    "minor: with Turnstile on, requests with bad tokens are never rate limited, and each one makes an outbound call "
    "to Cloudflare's siteverify: 20 bad tokens from one address mean 20 calls and 20 refusals, none of them a 429. "
    "The Turnstile check runs before the per-address limit. Likely fix: src/evidenceline/api/app.py (ask: count a "
    "failed check against the address, or check the limit first)."
)


def test_a_flood_of_bad_tokens_from_one_address_is_limited(siteverify: Siteverify) -> None:
    # Every question, a failed robot check included, counts against the generous limit on questions answered
    # without the model; it is fixed in code, so the test sets it low to reach it.
    settings = replace(from_env({"TURNSTILE_SECRET": LIVE_TURNSTILE_KEY}), no_model_per_hour=3)
    with TestClient(_app(settings)) as client:
        codes = [_ask(client, turnstile_token=f"bad-{n}").status_code for n in range(20)]
    assert 429 in codes
    assert len(siteverify.forms) < 20


# ===================================================================================================================
# 4. The proxy secret gate
# ===================================================================================================================

SECRET = "Zq8" * 16


@pytest.fixture
def gated() -> Iterator[tuple[TestClient, FakeClient]]:
    model = FakeClient(reply=GOOD)
    with TestClient(_app(Settings(proxy_secret=SECRET, client_ip_header="x-evidenceline-client-ip"), model)) as client:
        yield client, model


@pytest.mark.parametrize(
    "path",
    ["/mcp/", "/api/ask/", "/api//ask", "/API/ASK", "/Mcp", "/api/ask%2F", "/api/./ask", "/mcp;x", "/api/ask?x=1"],
)
def test_no_path_variant_serves_without_the_secret(gated: tuple[TestClient, FakeClient], path: str) -> None:
    client, model = gated
    for body in ({"question": QUESTION}, _rpc("tools/list", {})):
        response = client.post(path, json=body, headers=MCP_HEADERS, follow_redirects=False)
        assert response.status_code != 200 or "result" not in response.text, (path, response.text[:200])
        assert '"answered"' not in response.text
    assert model.calls == []


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH", "HEAD"])
@pytest.mark.parametrize("path", ["/mcp", "/api/ask"])
def test_every_method_is_gated(gated: tuple[TestClient, FakeClient], method: str, path: str) -> None:
    client, _ = gated
    assert client.request(method, path, headers=MCP_HEADERS).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {PROXY_SECRET_HEADER: ""},
        {PROXY_SECRET_HEADER: SECRET[:-1]},
        {PROXY_SECRET_HEADER: SECRET + "x"},
        {PROXY_SECRET_HEADER: SECRET.upper()},
        {PROXY_SECRET_HEADER: SECRET.lower()},
        {PROXY_SECRET_HEADER: f"Bearer {SECRET}"},
        {"authorization": f"Bearer {SECRET}"},
        {"x-evidenceline-proxy-secrets": SECRET},
        {"cookie": f"{PROXY_SECRET_HEADER}={SECRET}"},
    ],
    ids=[
        "empty",
        "one short",
        "one long",
        "upper case",
        "lower case",
        "bearer prefix",
        "authorization",
        "near name",
        "cookie",
    ],
)
def test_near_miss_secrets_are_refused(gated: tuple[TestClient, FakeClient], headers: dict[str, str]) -> None:
    client, model = gated
    assert _ask(client, headers).status_code == 403
    assert client.post("/mcp", json=_rpc("tools/list", {}), headers={**MCP_HEADERS, **headers}).status_code == 403
    assert client.post(f"/api/ask?{PROXY_SECRET_HEADER}={SECRET}", json={"question": QUESTION}).status_code == 403
    assert model.calls == []


def test_the_header_name_is_case_insensitive_and_the_secret_is_never_echoed(
    gated: tuple[TestClient, FakeClient],
) -> None:
    client, _ = gated
    upper = {PROXY_SECRET_HEADER.upper(): SECRET}
    served = _ask(client, upper)
    listed = client.post("/mcp", json=_rpc("tools/list", {}), headers={**MCP_HEADERS, **upper})
    refused = _ask(client, {PROXY_SECRET_HEADER: SECRET[:-1]})
    health = client.get("/api/health")
    assert served.status_code == 200
    assert listed.status_code == 200
    assert refused.json() == {"error": NOT_THROUGH_PROXY}
    for response in (served, listed, refused, health):
        assert SECRET not in response.text
        assert SECRET[:-1] not in response.text
        assert all(SECRET not in value for value in response.headers.values())


def test_refused_requests_do_not_use_up_the_limit_of_the_address_they_name(
    gated: tuple[TestClient, FakeClient],
) -> None:
    """A direct caller who types a visitor's address in the client IP header cannot spend that visitor's limit."""
    client, _ = gated
    victim = {"x-evidenceline-client-ip": "198.51.100.44"}
    for _ in range(40):
        assert _ask(client, victim).status_code == 403
        assert client.post("/mcp", json=_rpc("tools/list", {}), headers={**MCP_HEADERS, **victim}).status_code == 403
    assert _ask(client, {**victim, PROXY_SECRET_HEADER: SECRET}).status_code == 200


def test_health_stays_open_without_the_secret_and_says_so(gated: tuple[TestClient, FakeClient]) -> None:
    client, _ = gated
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["proxy_only"] is True
    assert health.json()["client_ip_from"] == "x-evidenceline-client-ip"


# ===================================================================================================================
# 5. lookup_limit after the NEMP 3.0 footnote change, over the hosted connector
# ===================================================================================================================


def _guideline(rule: str, key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(GUIDELINES.read_text(encoding="utf-8"))
    for entry in data["rules"]:
        if entry["id"] == rule:
            for limit in entry["limits"]:
                if limit["key"] == key:
                    found: dict[str, Any] = limit
                    return found
    raise AssertionError((rule, key))


@pytest.fixture
def connector() -> Iterator[TestClient]:
    with TestClient(_app()) as client:
        yield client


@pytest.mark.parametrize(
    ("analyte", "rule", "expected"),
    [
        ("PFOS", "nemp-3.0", ("PFOS+PFHxS", "sum", "PFOS on its own")),
        ("PFHxS", "nemp-3.0", ("PFOS+PFHxS", "sum", "PFHxS on its own")),
        ("pfos", " NEMP-3.0 ", ("PFOS+PFHxS", "sum", "PFOS on its own")),
        ("PFOS+PFHxS", "nemp-3.0", ("PFOS+PFHxS", "sum", "sum of PFOS and PFHxS")),
        ("PFHxS + PFOS", "nemp-3.0", ("PFOS+PFHxS", "sum", "sum of PFOS and PFHxS")),
        ("sum", "nemp-3.0", ("PFOS+PFHxS", "sum", "sum of PFOS and PFHxS")),
        ("PFOA", "nemp-3.0", ("PFOA", "single", "PFOA")),
        ("PFOS", "current", ("PFOS", "single", "PFOS")),
        ("PFHxS", "current", ("PFHxS", "single", "PFHxS")),
        ("PFOA", "Current", ("PFOA", "single", "PFOA")),
        ("PFBS", "current", ("PFBS", "single", "PFBS")),
    ],
    ids=[
        "PFOS alone, NEMP 3.0",
        "PFHxS alone, NEMP 3.0",
        "lower case and padding",
        "the sum, NEMP 3.0",
        "the sum, other order",
        "the word sum",
        "PFOA, NEMP 3.0",
        "PFOS, current",
        "PFHxS, current",
        "PFOA, current",
        "PFBS, current",
    ],
)
def test_lookup_limit_follows_the_data_file(
    connector: TestClient, analyte: str, rule: str, expected: tuple[str, str, str]
) -> None:
    key, applies_to, compared = expected
    info = _structured(_call(connector, "lookup_limit", {"analyte": analyte, "rule": rule}))
    source = _guideline(rule.strip().lower(), key)
    assert info["value"] == source["value"]
    assert Decimal(info["value"]) == Decimal(source["value"])
    assert isinstance(info["value"], str)  # an exact decimal string, never a float
    assert info["applies_to"] == applies_to
    assert info["compared_quantity"] == compared
    assert info["note"] == source["note"]
    assert info["unit"] == "ug/L"
    for text in (info["note"], info["compared_quantity"], *info["caveats"]):
        assert not any(dash in text for dash in DASHES), text
    if compared.endswith("on its own") and applies_to == "sum":
        assert f'"{FOOTNOTE_A}"' in info["caveats"][0]
        assert FOOTNOTE_A == "PFOS only, PFHxS only, and the sum of the two"
        assert (info["table"], info["page"], info["page_basis"]) == ("Table 4", "57", "PDF page")


def test_nemp_3_0_gives_pfos_and_pfhxs_each_the_same_value_as_their_sum(connector: TestClient) -> None:
    values = {
        analyte: _structured(_call(connector, "lookup_limit", {"analyte": analyte, "rule": "nemp-3.0"}))["value"]
        for analyte in ("PFOS", "PFHxS", "PFOS+PFHxS")
    }
    assert set(values.values()) == {"0.07"}


@pytest.mark.parametrize(
    ("analyte", "rule", "says"),
    [
        ("PFOS+PFHxS", "current", "screens PFOS and PFHxS separately"),
        ("sum", "current", "screens PFOS and PFHxS separately"),
        ("PFBS", "nemp-3.0", "has no drinking-water value for PFBS"),
    ],
    ids=["no sum under current", "the word sum under current", "no PFBS under NEMP 3.0"],
)
def test_values_a_rule_does_not_set_are_refused_not_guessed(
    connector: TestClient, analyte: str, rule: str, says: str
) -> None:
    reply = _call(connector, "lookup_limit", {"analyte": analyte, "rule": rule})
    assert reply["result"]["isError"] is True
    text = _tool_text(reply)
    assert says in text
    assert not re.search(r"\d+\.\d+ ?ug/L", text), text  # no stray value offered in its place
    assert not any(dash in text for dash in DASHES)


def test_compare_rules_screens_pfos_and_pfhxs_alone_and_the_sum_only_under_nemp_3_0(connector: TestClient) -> None:
    info = _structured(_call(connector, "compare_rules", {"well": "MB2", "date": "Sep 2025"}))
    screens = {rule["rule"]: {s["compared_quantity"]: s for s in rule["screens"]} for rule in info["rules"]}
    nemp = screens["nemp-3.0"]
    assert {"sum of PFOS and PFHxS", "PFOS on its own", "PFHxS on its own"} <= set(nemp)
    assert {nemp[q]["limit"] for q in ("sum of PFOS and PFHxS", "PFOS on its own", "PFHxS on its own")} == {"0.07"}
    current = screens["current"]
    assert "sum of PFOS and PFHxS" not in current
    assert (current["PFOS"]["limit"], current["PFHxS"]["limit"]) == ("0.008", "0.03")
    # The lab values are the same under both rules: only the limit and how it is applied differ.
    assert nemp["PFOS on its own"]["compared_value"] == current["PFOS"]["compared_value"]


def test_tool_description_and_instructions_state_footnote_a_without_picking_a_rule(connector: TestClient) -> None:
    tools = connector.post("/mcp", json=_rpc("tools/list", {}), headers=MCP_HEADERS).json()["result"]["tools"]
    description = next(tool["description"] for tool in tools if tool["name"] == "lookup_limit")
    assert "PFOS alone, PFHxS alone and their sum" in description
    assert "Table 4, footnote a" in description
    assert "either" not in description.split("Under 'nemp-3.0'")[1].split(".")[0].replace("asking for either", "")
    instructions = stdio_server.INSTRUCTIONS
    assert "PFOS alone and PFHxS alone, against 0.07 ug/L (Table 4, footnote a)" in instructions
    assert "never pick one" in instructions
    for text in (description, instructions):
        assert not any(dash in text for dash in DASHES)


def test_the_question_box_shows_both_pfos_values_with_the_footnote(connector: TestClient) -> None:
    body = connector.post("/api/ask", json={"question": "What is the drinking-water limit for PFOS?"}).json()
    values = {value["rule"]: value for value in body["guideline_values"] if value["analyte"] == "PFOS"}
    assert set(values) == {"nemp-3.0", "current"}
    assert values["nemp-3.0"]["value"] == "0.07"
    assert "also applies to PFOS on its own" in values["nemp-3.0"]["note"]
    assert FOOTNOTE_A in values["nemp-3.0"]["note"]
    assert values["current"]["value"] == "0.008"


# ===================================================================================================================
# 6. render.yaml against the settings the app reads
# ===================================================================================================================


def _render_env() -> dict[str, str | None]:
    """envVars from render.yaml: key to value, or None for a 'sync: false' secret Render asks for."""
    text = RENDER_YAML.read_text(encoding="utf-8")
    block = text.split("envVars:", 1)[1]
    env: dict[str, str | None] = {}
    for match in re.finditer(r"- key: (\S+)\n\s+(value|sync): (.+)", block):
        key, kind, raw = match.groups()
        env[key] = raw.strip().strip('"') if kind == "value" else None
    return env


def _render_field(name: str) -> str:
    text = RENDER_YAML.read_text(encoding="utf-8")
    match = re.search(rf"^\s+{name}: >-\n((?:\s{{6,}}.+\n)+)|^\s+{name}: (.+)$", text, flags=re.MULTILINE)
    assert match is not None, name
    return " ".join((match.group(1) or match.group(2)).split())


def test_render_sets_every_setting_the_api_reads_and_nothing_it_does_not() -> None:
    env = _render_env()
    read_by_settings = set(
        re.findall(r'"((?:EVIDENCELINE|TURNSTILE)_[A-Z_]+)"', SETTINGS_PY.read_text(encoding="utf-8"))
    )
    assert read_by_settings <= set(env), read_by_settings - set(env)
    source = "".join(path.read_text(encoding="utf-8") for path in (REPO_ROOT / "src" / "evidenceline").rglob("*.py"))
    for key in env:
        if key != "PYTHON_VERSION":  # read by Render itself
            assert f'"{key}"' in source, f"{key} is set in render.yaml but read nowhere"


def test_render_secrets_are_asked_for_not_written_down() -> None:
    env = _render_env()
    assert {key for key, value in env.items() if value is None} == {
        "ANTHROPIC_API_KEY",
        "EVIDENCELINE_PROXY_SECRET",
        "TURNSTILE_SECRET",
    }
    assert not re.search(r"sk-ant-|0x4A{3}", RENDER_YAML.read_text(encoding="utf-8"))


def test_render_values_parse_into_the_settings_the_site_needs() -> None:
    env = {key: value for key, value in _render_env().items() if value is not None}
    settings = from_env({**env, "EVIDENCELINE_PROXY_SECRET": "x" * 40})
    assert settings.allowed_origins == (SITE,)
    assert settings.client_ip_header == "x-evidenceline-client-ip"
    assert settings.trust_client_ip_header is True
    assert settings.turnstile_secret is None  # Turnstile stays off until the owner sets it
    assert settings.ask_per_hour <= settings.ask_per_day <= settings.answers_per_day
    assert (settings.ask_per_hour, settings.ask_per_day, settings.answers_per_day, settings.mcp_per_hour) == (
        int(env["EVIDENCELINE_ASK_PER_HOUR"]),
        int(env["EVIDENCELINE_ASK_PER_DAY"]),
        int(env["EVIDENCELINE_ANSWERS_PER_DAY"]),
        int(env["EVIDENCELINE_MCP_PER_HOUR"]),
    )


def test_render_origin_matches_the_worker_route_and_the_refusal_message() -> None:
    route = re.search(r'"pattern": "([^"]+)", "custom_domain": true', WRANGLER.read_text(encoding="utf-8"))
    assert route is not None
    assert _render_env()["EVIDENCELINE_ALLOWED_ORIGINS"] == f"https://{route.group(1)}" == SITE
    assert SITE in NOT_THROUGH_PROXY


def test_render_model_matches_the_code_default_and_the_prepared_answers() -> None:
    answers: dict[str, Any] = json.loads(ANSWERS.read_text(encoding="utf-8"))
    assert _render_env()["EVIDENCELINE_MODEL"] == DEFAULT_MODEL == answers["model"]


def test_render_python_version_is_full_and_allowed() -> None:
    version = _render_env()["PYTHON_VERSION"] or ""
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    requires = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["requires-python"]
    minimum = tuple(int(part) for part in re.findall(r"\d+", requires)[:2])
    assert tuple(int(part) for part in version.split(".")[:2]) >= minimum


def test_render_start_command_builds_the_app_and_health_passes_behind_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start command's factory, with render.yaml's environment and a proxy secret, answers the health check path
    without the secret, and loads the demo identifier file."""
    start = _render_field("startCommand")
    target = re.search(r"uvicorn (\S+):(\S+) --factory", start)
    assert target is not None
    assert (target.group(1), target.group(2)) == ("evidenceline.api.app", "create_app")
    assert "--no-proxy-headers" in start  # the client address comes from the proxy's own header, not X-Forwarded-For
    for key, value in _render_env().items():
        if value is not None and key != "PYTHON_VERSION":
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("EVIDENCELINE_PROXY_SECRET", "x" * 40)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    redact.reset_session()
    factory: Callable[[], FastAPI] = getattr(app_module, target.group(2))
    with TestClient(factory()) as client:
        health = client.get(_render_field("healthCheckPath"))
        refused = client.post("/api/ask", json={"question": QUESTION})
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["redaction"] == "Built-in identifier file loaded (builtin:fds01-demo)."
    assert body["proxy_only"] is True
    assert body["live_answers"] is False  # no key yet: passages only, as the render.yaml comment says
    assert body["turnstile"] is False
    assert refused.status_code == 403


def test_render_corpus_folder_is_where_the_index_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _render_env()["EVIDENCELINE_CORPUS_DIR"] or ""
    assert folder.startswith("/opt/render/project/src/")
    monkeypatch.setenv("EVIDENCELINE_CORPUS_DIR", folder)
    assert Path(folder) in default_index_path().parents
