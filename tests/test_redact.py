"""The redaction guard rail: client identifiers become placeholders before tool output reaches a model.

Every identifier used here is fictional.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict

from evidenceline import redact
from evidenceline.drafting import fill_placeholders, redact_filled
from evidenceline.errors import EvidencelineError
from evidenceline.models import SourceRef
from evidenceline.redact import Redactor, apply_redaction, load_config, register_tools, reset_session, restore

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "redact.example.toml"

CONFIG = """
[[client]]
names = ["Quokka Ridge Holdings Pty Ltd", "Quokka Ridge Holdings", "QRH"]

[[client]]
names = ["Banksia Flats Transport"]

[[site]]
names = ["Sampleton Fuel Depot"]

[[person]]
names = ["Jane Citizen"]

[[address]]
names = ["42 Imaginary Road Sampleton"]

[[phone]]
names = ["08 5550 1234"]
"""

RAW_VALUES = (
    "Quokka Ridge Holdings Pty Ltd",
    "Quokka Ridge Holdings",
    "QRH",
    "Banksia Flats Transport",
    "Sampleton Fuel Depot",
    "Jane Citizen",
    "42 Imaginary Road Sampleton",
    "7 Other Street, East Sampleton, WA 6999",
    "Lot 9999",
    "Deposited Plan 99999",
    "site.contact@example.com",
    "08 5550 1234",
    "5550 1234",
    "0491 570 156",
)

CLIENT_TEXT = (
    "QRH (Quokka Ridge Holdings Pty Ltd) owns Lot 9999 on Deposited Plan 99999 at 42 Imaginary Road Sampleton. "
    "Banksia Flats Transport leases 7 Other Street, East Sampleton, WA 6999. Contact Jane Citizen on (08) 5550 1234 "
    "or 0491 570 156, or site.contact@example.com, about the Sampleton Fuel Depot."
)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "redact.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


@pytest.fixture
def audit_log(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


@pytest.fixture
def redactor(config_file: Path, audit_log: Path) -> Redactor:
    return Redactor(load_config(config_file), audit_log)


@pytest.fixture
def env_session(config_file: Path, audit_log: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The process-wide session, pointed at the temp config and audit log."""
    monkeypatch.setenv(redact.CONFIG_ENV, str(config_file))
    monkeypatch.setenv(redact.AUDIT_ENV, str(audit_log))
    reset_session()
    yield
    reset_session()


def assert_no_raw_values(blob: str) -> None:
    for raw in RAW_VALUES:
        assert raw.lower() not in blob.lower(), raw


# --- what is redacted --------------------------------------------------------------------------------------------


def test_client_text_is_fully_redacted(redactor: Redactor) -> None:
    out = redactor.redact_text(CLIENT_TEXT)
    assert out == (
        "[CLIENT-1] ([CLIENT-1]) owns [LOT-1] at [ADDRESS-1]. [CLIENT-2] leases [ADDRESS-2]. Contact [PERSON-1] on "
        "[PHONE-1] or [PHONE-2], or [EMAIL-1], about the [SITE-1]."
    )
    assert_no_raw_values(out)


def test_names_match_any_case_and_spacing_but_only_whole_words(redactor: Redactor) -> None:
    assert redactor.redact_text("quokka   ridge HOLDINGS and qrh") == "[CLIENT-1] and [CLIENT-1]"
    assert redactor.redact_text("QRHX and XQRH and Jane Citizenship") == "QRHX and XQRH and Jane Citizenship"


@pytest.mark.parametrize(
    "text",
    [
        "Lot 123",
        "LOT 45A",
        "Lot 9999 on Deposited Plan 99999",
        "Lot 12 on DP 34567",
        "Lot 3 on Strata Plan 4567",
    ],
)
def test_lots(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(f"The site is {text}.") == "The site is [LOT-1]."


@pytest.mark.parametrize(
    "text",
    [
        "15 Example Road Sampleton WA",
        "15 Example Road, Sampleton, WA 6999",
        "15A Long Creek Drive North Sampleton WA 6999",
        "Unit 3/15 Example Avenue Sampleton WA",
        "Lot 7 Example Street Sampleton Western Australia",
        "210-214 Imaginary Highway East Sampleton WA 6999",
    ],
)
def test_street_addresses(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(f"Located at {text}.") == "Located at [ADDRESS-1]."


@pytest.mark.parametrize(
    "text", ["jane.citizen@example.com", "Site.Contact+pfas@sub.example.com.au", "a_b@example.org"]
)
def test_emails(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(f"Email {text} today.") == "Email [EMAIL-1] today."


@pytest.mark.parametrize(
    "text",
    [
        "08 5550 1234",
        "(08) 5550 1234",
        "+61 8 5550 1234",
        "+61 (0)8 5550 1234",
        "0855501234",
        "08-5550-1234",
    ],
)
def test_landline_formats_share_one_placeholder(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(f"Call {text} now.") == "Call [PHONE-1] now."


@pytest.mark.parametrize("text", ["0491 570 156", "+61 491 570 156", "0491570156", "1800 555 123", "1300 555 123"])
def test_mobile_and_service_numbers(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(f"Call {text} now.") == "Call [PHONE-1] now."


def test_same_phone_in_different_formats_is_one_identity(redactor: Redactor) -> None:
    out = redactor.redact_text("0491 570 156, +61 491 570 156, 08 5550 1234 and (08) 5550 1234")
    assert out == "[PHONE-1], [PHONE-1], [PHONE-2] and [PHONE-2]"


@pytest.mark.parametrize(
    "text",
    [
        "PFOS was 0.038 ug/L on 2025-09-16 (SYN-250916, mb2_round3_lab.csv row 14).",
        "Sample MB2-20250916 from well MB2 at site FDS-01.",
        "The sum 0.038 + 0.019 = 0.057 ug/L is below 0.07 ug/L (NEMP 3.0, Table 4, PDF page 57).",
        "Rounds on 12 March 2024, 19 November 2024 and 5 May 2026; 14 rows each; a lot of 12 samples.",
        "Contaminated Sites Act 2003, 6 Guideline Street values.",
    ],
)
def test_monitoring_data_is_left_alone(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "15 EXAMPLE ROAD, SAMPLETON WA 6999",
        "15 example road, sampleton wa",
        "15 Example RD., Sampleton, W.A.",
        "15 Example Road Sampleton Wa 6999",
        "Sampleton WA 6999, 15 Example Road",
        "SAMPLETON W.A., 15 EXAMPLE ROAD",
    ],
)
def test_street_addresses_in_any_case_and_order(redactor: Redactor, text: str) -> None:
    out = redactor.redact_text(f"Located at {text} today.")
    assert out == "Located at [ADDRESS-1] today.", out


@pytest.mark.parametrize(
    "text",
    [
        "Banksia-Flats-Transport",
        "Banksia_Flats_Transport",
        "BanksiaFlatsTransport",
        "www.banksiaflatstransport.com.au",
        "Bank\u00adsia Flats Transport",
        "Banksia\u200bFlats Transport",
        "Banksia\r\nFlats Transport",
        "'Banksia\\nFlats Transport'",
        "x\\nBanksia Flats Transport",
    ],
)
def test_listed_names_match_across_separators_and_invisible_characters(redactor: Redactor, text: str) -> None:
    out = redactor.redact_text(f"Client: {text}.")
    assert "banksia" not in out.casefold(), out
    assert "[CLIENT-1]" in out


def test_one_placeholder_for_every_spelling_of_a_listed_name(redactor: Redactor) -> None:
    out = redactor.redact_text("Banksia Flats Transport, BANKSIA-FLATS-TRANSPORT and banksiaflatstransport")
    assert out == "[CLIENT-1], [CLIENT-1] and [CLIENT-1]"


def test_redact_value_covers_every_type_and_writes_no_audit_line(redactor: Redactor, audit_log: Path) -> None:
    counts: Counter[str] = Counter()
    out = redactor.redact_value({"well": ["Banksia\nFlats Transport"], "Jane Citizen": 1, "k": 3}, counts)
    assert out == {"well": ["[CLIENT-1]"], "[PERSON-1]": 1, "k": 3}
    assert counts == Counter({"CLIENT": 1, "PERSON": 1})
    assert not audit_log.exists()


# --- stable numbering and restore -------------------------------------------------------------------------------


def test_numbering_is_stable_across_calls_and_counts_per_type(redactor: Redactor) -> None:
    first = redactor.redact_text("Banksia Flats Transport and Lot 5")
    second = redactor.redact_text("Lot 6, QRH, Lot 5 and banksia flats transport")
    assert first == "[CLIENT-1] and [LOT-1]"
    assert second == "[LOT-2], [CLIENT-2], [LOT-1] and [CLIENT-1]"


def test_configured_address_and_built_in_pattern_share_a_placeholder(redactor: Redactor) -> None:
    assert redactor.redact_text("42 Imaginary Road Sampleton") == "[ADDRESS-1]"
    assert redactor.redact_text("42 Imaginary Road Sampleton WA 6999") == "[ADDRESS-1] WA 6999"


def test_restore_round_trip(redactor: Redactor) -> None:
    text = "Jane Citizen (site.contact@example.com, 0491 570 156) visited Lot 9999 for Banksia Flats Transport."
    redacted = redactor.redact_text(text)
    assert_no_raw_values(redacted)
    assert redactor.restore(redacted) == text


def test_restore_uses_the_first_configured_name_for_aliases(redactor: Redactor) -> None:
    assert redactor.restore(redactor.redact_text("QRH")) == "Quokka Ridge Holdings Pty Ltd"


def test_restore_leaves_unknown_placeholders(redactor: Redactor) -> None:
    assert redactor.restore("[CLIENT-7] and text") == "[CLIENT-7] and text"


def test_redaction_is_idempotent(redactor: Redactor) -> None:
    once = redactor.redact_text(CLIENT_TEXT)
    assert redactor.redact_text(once) == once


def test_fill_result_is_redacted_around_its_values_not_through_them(redactor: Redactor) -> None:
    """'Lot ' before a filled value must not turn the value into a lot number; the words around it are redacted."""
    filled = fill_placeholders(
        "Lot {PFOS|MB2|Sep 2025} at 42 Imaginary Road Sampleton WA, "
        "then {limit|PFOS|current} for Quokka Ridge Holdings.",
        "MB2",
    )
    counts: Counter[str] = Counter()
    safe = redact_filled(filled, lambda value: redactor.redact_value(value, counts))
    assert [v.text for v in safe.values] == ["0.038 ug/L", "0.008 ug/L"]
    for value in safe.values:
        assert safe.text[value.start : value.end] == value.text
    assert safe.text.startswith("Lot 0.038 ug/L at [ADDRESS-")
    assert "[CLIENT-" in safe.text
    assert "[CLIENT-" in safe.annotated_text
    assert "0.038 ug/L [1]" in safe.annotated_text
    assert_no_raw_values(safe.model_dump_json())
    # each stretch of words is redacted once and used in both texts
    assert counts["ADDRESS"] == 1
    assert counts["CLIENT"] == 1


def test_fill_result_with_nothing_to_redact_is_unchanged(redactor: Redactor) -> None:
    filled = fill_placeholders("PFOS was {PFOS|MB2|Sep 2025} against {limit|PFOS|current}.", "MB2")
    assert redact_filled(filled, redactor.redact_value) == filled


# --- walking models and containers -----------------------------------------------------------------------------


class Inner(BaseModel):
    model_config = ConfigDict(frozen=True)

    note: str
    value: str
    count: int


class Outer(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    inner: Inner
    items: list[Inner]
    tags: dict[str, str]
    pair: tuple[str, int]
    maybe: str | None
    source: SourceRef


def test_nested_models_are_walked(redactor: Redactor) -> None:
    obj = Outer(
        title="Report for QRH",
        inner=Inner(note="Contact Jane Citizen", value="0.038", count=3),
        items=[Inner(note="Lot 9999", value="0.019", count=1), Inner(note="plain", value="0.057", count=2)],
        tags={"Banksia Flats Transport": "site.contact@example.com"},
        pair=("08 5550 1234", 7),
        maybe=None,
        source=SourceRef(kind="lab result", reference="Sampleton Fuel Depot row 14", value="0.038"),
    )
    out = redactor.apply(obj, tool="test")
    assert isinstance(out, Outer)
    assert out.title == "Report for [CLIENT-1]"
    assert out.inner == Inner(note="Contact [PERSON-1]", value="0.038", count=3)
    assert [i.note for i in out.items] == ["[LOT-1]", "plain"]
    assert out.tags == {"[CLIENT-2]": "[EMAIL-1]"}
    assert out.pair == ("[PHONE-1]", 7)
    assert out.maybe is None
    assert out.source.reference == "[SITE-1] row 14"
    assert_no_raw_values(out.model_dump_json())
    assert obj.title == "Report for QRH"  # the original is not changed


def test_plain_containers_and_sets(redactor: Redactor) -> None:
    out = redactor.apply({"a": ["QRH", ("Lot 5", 1)], "b": {"Jane Citizen"}, "c": frozenset({"x"})}, tool="test")
    assert out == {"a": ["[CLIENT-1]", ("[LOT-1]", 1)], "b": {"[PERSON-1]"}, "c": frozenset({"x"})}


# --- audit log ---------------------------------------------------------------------------------------------------


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_audit_log_has_counts_and_never_values(redactor: Redactor, audit_log: Path) -> None:
    redactor.apply(CLIENT_TEXT, tool="check_paragraph")
    redactor.apply("nothing to hide", tool="get_results")
    lines = _audit_lines(audit_log)
    assert len(lines) == 2
    assert set(lines[0]) == {"time", "tool", "counts"}
    assert lines[0]["tool"] == "check_paragraph"
    assert lines[0]["counts"] == {"ADDRESS": 2, "CLIENT": 3, "EMAIL": 1, "LOT": 1, "PERSON": 1, "PHONE": 2, "SITE": 1}
    assert lines[1]["counts"] == {}
    assert_no_raw_values(audit_log.read_text(encoding="utf-8"))


def test_audit_tool_name_cannot_carry_a_value(redactor: Redactor, audit_log: Path) -> None:
    redactor.apply("QRH", tool="Jane Citizen")
    assert _audit_lines(audit_log)[0]["tool"] == "unnamed"


def test_audit_write_failure_is_reported_not_raised(config_file: Path, tmp_path: Path) -> None:
    blocked = tmp_path / "a-directory"
    blocked.mkdir()
    r = Redactor(load_config(config_file), blocked)
    assert r.apply("QRH", tool="t") == "[CLIENT-1]"
    assert "1 line(s) could not be written" in r.summary().audit_log


# --- configuration -----------------------------------------------------------------------------------------------


def test_example_file_is_valid_and_covers_every_type(tmp_path: Path) -> None:
    config = load_config(EXAMPLE)
    assert {identity.type for identity in config.identities} == set(redact.CONFIG_TYPES.values())
    r = Redactor(config, None)
    for identity in config.identities:
        for name in identity.names:
            out = r.redact_text(name)
            assert name.lower() not in out.lower()
            assert out.startswith(f"[{identity.type}-")


@pytest.mark.parametrize(
    ("toml", "fragment"),
    [
        ('[[Acme_Mining]]\nnames = ["x y"]\n', "a table that is not one of"),
        ('client = ["QRH"]\n', "must be written as [[client]] blocks"),
        ('[[client]]\nname = ["QRH"]\n', "exactly one key: names"),
        ("[[client]]\nnames = []\n", "non-empty list"),
        ('[[client]]\nnames = ["Q"]\n', "at least 2 characters"),
        ("[[client]]\nnames = [3]\n", "name 1 must be text"),
        ('[[client]\nnames = ["QRH"]\n', "not valid TOML"),
    ],
)
def test_bad_config_fails_closed_without_echoing_values(tmp_path: Path, toml: str, fragment: str) -> None:
    path = tmp_path / "redact.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(EvidencelineError) as info:
        load_config(path)
    message = str(info.value)
    assert fragment in message
    assert "Acme" not in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_env_path_is_an_error_not_built_in_patterns_only(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, value)
    with pytest.raises(EvidencelineError, match="is set but empty"):
        load_config()


def test_missing_file_named_by_env_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "Client Acme" / "missing.toml"))
    with pytest.raises(EvidencelineError) as info:
        load_config()
    assert "No redaction file at the path in EVIDENCELINE_REDACT" in str(info.value)
    assert "Acme" not in str(info.value)


def test_missing_default_file_means_built_in_patterns_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(redact.CONFIG_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config = load_config()
    assert config.identities == ()
    assert "built-in patterns only" in config.status
    assert Redactor(config, None).redact_text("QRH at Lot 4") == "QRH at [LOT-1]"


# --- module session and the MCP tool -----------------------------------------------------------------------------


@pytest.mark.usefixtures("env_session")
def test_module_functions_share_one_session(audit_log: Path) -> None:
    first = apply_redaction({"text": "QRH at Lot 9999"}, tool="get_results")
    second = apply_redaction("Lot 9999 and QRH", tool="check_paragraph")
    assert first == {"text": "[CLIENT-1] at [LOT-1]"}
    assert second == "[LOT-1] and [CLIENT-1]"
    assert restore(second) == "Lot 9999 and Quokka Ridge Holdings Pty Ltd"
    assert [line["tool"] for line in _audit_lines(audit_log)] == ["get_results", "check_paragraph"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.usefixtures("env_session")
async def test_show_redactions_returns_names_and_counts_only(audit_log: Path) -> None:
    apply_redaction(CLIENT_TEXT, tool="check_paragraph")
    apply_redaction("QRH again", tool="check_paragraph")
    server = MCPServer(name="redact-test")
    register_tools(server)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"show_redactions"}
        assert tools["show_redactions"].annotations is not None
        assert tools["show_redactions"].annotations.read_only_hint is True
        result = await client.call_tool("show_redactions", {})
    assert not result.is_error
    content = cast(dict[str, Any], result.structured_content)
    assert isinstance(content, dict)
    rows = cast(list[dict[str, Any]], content["placeholders"])
    placeholders = {row["placeholder"]: row["replacements"] for row in rows}
    assert placeholders["[CLIENT-1]"] == 3
    assert placeholders["[PHONE-1]"] == 1
    assert content["replacements_by_type"] == {
        "ADDRESS": 2,
        "CLIENT": 4,
        "EMAIL": 1,
        "LOT": 1,
        "PERSON": 1,
        "PHONE": 2,
        "SITE": 1,
    }
    assert content["identities_configured"] == {"ADDRESS": 1, "CLIENT": 2, "PERSON": 1, "PHONE": 1, "SITE": 1}
    everything = json.dumps(content) + " ".join(str(block) for block in result.content)
    assert_no_raw_values(everything)
    assert str(audit_log.parent) not in everything
    assert chr(0x2014) not in everything
    assert chr(0x2013) not in everything


@pytest.mark.anyio
async def test_show_redactions_reports_a_broken_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, str(tmp_path / "missing.toml"))
    reset_session()
    try:
        server = MCPServer(name="redact-test")
        register_tools(server)
        async with Client(server) as client:
            result = await client.call_tool("show_redactions", {})
        assert result.is_error
    finally:
        reset_session()


def test_counts_argument_collects_per_type(redactor: Redactor) -> None:
    counts: Counter[str] = Counter()
    redactor.redact_text("QRH, QRH and Lot 1", counts)
    assert counts == Counter({"CLIENT": 2, "LOT": 1})


# --- the built-in demo identifier file (EVIDENCELINE_REDACT=builtin:fds01-demo) ---------------------------------------


@pytest.mark.parametrize("value", ["builtin:fds01-demo", " BUILTIN:FDS01-demo "])
def test_builtin_demo_file_is_selected_by_the_environment(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, value)
    config = load_config()
    assert config.builtin == "fds01-demo"
    assert config.status == "Built-in identifier file loaded (builtin:fds01-demo)."
    assert sorted(identity.type for identity in config.identities) == ["ADDRESS", "CLIENT"]
    redactor = Redactor(config, None)
    text = "HARBOURLINE-LOGISTICS owns 12 Example Rd, Welshpool; harbourline_logistics wrote from 12 example road."
    assert redactor.redact_text(text) == "[CLIENT-1] owns [ADDRESS-1]; [CLIENT-1] wrote from [ADDRESS-1]."


def test_builtin_demo_file_is_packaged_and_lists_only_the_fictional_site() -> None:
    config = redact.load_builtin("fds01-demo")
    names = [name for identity in config.identities for name in identity.names]
    assert all("harbour" in name.casefold() or "example r" in name.casefold() for name in names)
    header = (REPO_ROOT / "src" / "evidenceline" / "data" / "fds01_site" / "field_sheet.csv").read_text(
        encoding="utf-8"
    )
    assert "# Client: Harbourline Logistics (fictional)" in header
    assert "# Site address: 12 Example Road, Welshpool WA (fictional)" in header


@pytest.mark.parametrize("value", ["builtin:Client Acme", "builtin:", "builtin: "])
def test_unknown_builtin_file_fails_closed_without_echoing(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(redact.CONFIG_ENV, value)
    with pytest.raises(EvidencelineError) as info:
        load_config()
    assert "builtin:fds01-demo" in str(info.value)
    assert "Acme" not in str(info.value)


def test_builtin_explanation_says_what_is_and_is_not_redacted() -> None:
    summary = Redactor(redact.load_builtin("fds01-demo"), None).summary()
    assert "fictional client name and site address of the sample site FDS-01" in summary.explanation
    assert "[CLIENT-1]" in summary.explanation
    assert "Any other client, site or person's name is NOT redacted" in summary.explanation
    assert summary.identities_configured == {"ADDRESS": 1, "CLIENT": 1}
    assert not any(dash in summary.explanation for dash in ("\u2013", "\u2014"))


# --- names inside file names, codes and links ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("QRH_Phase2_DSI.pdf", "[CLIENT-1]_Phase2_DSI.pdf"),
        ("DSI_QRH.pdf", "DSI_[CLIENT-1].pdf"),
        ("QRH2025 and 2025QRH", "[CLIENT-1]2025 and 2025[CLIENT-1]"),
        ("BanksiaFlatsTransportDSI", "[CLIENT-1]DSI"),
        ("draftBanksiaFlatsTransport", "draft[CLIENT-1]"),
        ("https://example.com/c/Banksia%20Flats%20Transport/report", "https://example.com/c/[CLIENT-1]/report"),
        ("Jane_Citizen_notes.txt", "[PERSON-1]_notes.txt"),
    ],
)
def test_listed_names_are_found_inside_file_names_codes_and_links(redactor: Redactor, text: str, expected: str) -> None:
    assert redactor.redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Jane Citizenship",
        "Janet Citizen",
        "Sampleton Fuel Depots",
        "142 Imaginary Road Sampleton",
        "QRHX, XQRH and QRHs",
        "Banksia Flats Transporter",
    ],
)
def test_a_listed_name_inside_a_longer_word_or_number_is_left_alone(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(text) == text


def test_an_identifier_file_that_lists_nothing_is_described_as_loaded(tmp_path: Path) -> None:
    path = tmp_path / "redact.toml"
    path.write_text("# nothing listed yet\n", encoding="utf-8")
    summary = Redactor(load_config(path), None).summary()
    assert summary.identifier_file.startswith("Identifier file loaded")
    assert "No identifier file is loaded" not in summary.explanation
    assert "lists no names" in summary.explanation
    assert "NOT redacted" in summary.explanation
    assert not any(dash in summary.explanation for dash in ("\u2013", "\u2014"))


def test_builtin_demo_file_catches_every_spelling_of_the_address_in_full() -> None:
    redactor = Redactor(redact.load_builtin("fds01-demo"), None)
    for text in (
        "Example Rd, WELSHPOOL",
        "12 Example Road Welshpool Western Australia 6106",
        "example road, welshpool wa 6106",
        "12 Example Rd, Welshpool 6106",
        "Harbour Line and HARBOUR-LINE",
    ):
        out = redactor.redact_text(f"Samples at {text} in 2025.")
        assert "welshpool" not in out.casefold(), out
        assert "6106" not in out, out
        assert "harbour" not in out.casefold(), out
