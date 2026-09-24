"""search_guidelines and its MCP tool, on a small synthetic index built in a temp folder.

The passages are synthetic text written for these tests (labelled so), not quotes from guidance documents. The
real corpus is exercised by test_guidance_golden.py, which skips when the corpus has not been fetched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from evidenceline.errors import EvidencelineError
from evidenceline.guidance import search as search_module
from evidenceline.guidance.chunking import Chunk
from evidenceline.guidance.index import INDEX_NAME, IndexedDocument, build_index
from evidenceline.guidance.manifest import CACHE_ENV, CorpusDocument, load_manifest
from evidenceline.guidance.scope import NUMERIC_NOTE
from evidenceline.guidance.search import EDITION_NOTE, EXCERPT_TOKENS, search_guidelines
from evidenceline.guidance.tools import register_tools, strict_tools

SYN = "Synthetic test text."
LONG_TAIL = " ".join(f"filler{i}" for i in range(120))


def _doc(ident: str, lane: str, fmt: str = "pdf", *, available: bool = True) -> CorpusDocument:
    template = next(d for d in load_manifest() if d.id == "nepm-b1")
    return replace(
        template,
        id=ident,
        title=f"Synthetic document {ident}",
        short_title=f"Synthetic {ident}",
        edition="Synthetic edition",
        format=fmt,  # type: ignore[arg-type]
        licence_lane=lane,  # type: ignore[arg-type]
        available=available,
        availability_note="" if available else "Synthetic: not indexed on purpose.",
        official_url=f"https://example.org/{ident}",
    )


CHUNKS = [
    Chunk(
        "syn-a",
        0,
        10,
        "5",
        "printed on the page",
        "2.1 Groundwater sampling",
        f"{SYN} Groundwater sampling wells must be purged before sampling. {LONG_TAIL}",
    ),
    Chunk(
        "syn-a",
        1,
        11,
        None,
        "not derived",
        "2.2 Drinking water",
        f"{SYN} Potable water supplies are assessed against the drinking water guideline value.",
    ),
    Chunk(
        "syn-b",
        0,
        3,
        "3",
        "inferred from neighbouring pages",
        "Checklist",
        f"{SYN} A detailed site investigation report should include a conceptual site model. {LONG_TAIL}",
    ),
    Chunk(
        "syn-c",
        0,
        None,
        None,
        None,
        "Measurement",
        f"{SYN} Perfluorooctane sulfonate is measured by liquid chromatography. {LONG_TAIL}",
    ),
]
DOCS = (_doc("syn-a", "A"), _doc("syn-b", "B"), _doc("syn-c", "C", "markdown"), _doc("syn-x", "A", available=False))


@pytest.fixture(scope="module")
def index_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("guidance") / INDEX_NAME
    documents = [IndexedDocument(d, "0" * 64, 20, (), 1) for d in ("syn-a", "syn-b", "syn-c")]
    build_index(path, documents, CHUNKS)
    return path


def ask(index_path: Path, question: str, k: int = 5) -> Any:
    return search_guidelines(question, k, index_path=index_path, manifest=DOCS)


# --- results ----------------------------------------------------------------------------------------------------


def test_passages_carry_edition_pages_link_and_notice(index_path: Path) -> None:
    out = ask(index_path, "How are groundwater wells sampled?")
    assert out.status == "passages found"
    top = out.passages[0]
    assert (top.document_id, top.pdf_page, top.printed_page) == ("syn-a", 10, "5")
    assert top.location == "p. 5 (PDF p. 10), 2.1 Groundwater sampling"
    assert top.link == "https://example.org/syn-a#page=10"
    assert top.edition == "Synthetic edition"
    assert top.notice
    assert top.licence_lane == "A"
    assert "groundwater" in top.matched
    assert EDITION_NOTE in out.notes
    assert "not an answer or advice" in out.explanation
    assert any("syn-x" in line or "Synthetic syn-x" in line for line in out.unavailable_documents)


def test_synonyms_find_potable_when_asked_about_drinking_water(index_path: Path) -> None:
    out = ask(index_path, "Which potable supplies are assessed?")
    assert out.passages[0].pdf_page == 11
    assert out.passages[0].printed_page is None
    assert "printed page number not derived" in out.passages[0].location
    assert "use the PDF page" in out.passages[0].note


def test_long_chemical_name_matches_the_acronym(index_path: Path) -> None:
    out = ask(index_path, "How is PFOS measured?")
    top = out.passages[0]
    assert top.document_id == "syn-c"
    assert top.pdf_page is None
    assert top.location == "section 'Measurement' (web page, no page numbers)"
    assert top.link == "https://example.org/syn-c"


def test_excerpts_are_shorter_for_restricted_licences(index_path: Path) -> None:
    lane_a = ask(index_path, "groundwater sampling wells purged").passages[0]
    lane_b = ask(index_path, "detailed site investigation conceptual model").passages[0]
    lane_c = ask(index_path, "perfluorooctane sulfonate liquid chromatography").passages[0]
    assert lane_a.excerpt_words <= EXCERPT_TOKENS["A"] + 1
    assert lane_b.excerpt_words <= EXCERPT_TOKENS["B"] + 1
    assert lane_c.excerpt_words <= EXCERPT_TOKENS["C"] + 1
    assert lane_c.excerpt_words < lane_b.excerpt_words < lane_a.excerpt_words
    assert "licence" in lane_b.note
    assert "licence" in lane_c.note
    assert lane_b.location.endswith("Checklist")
    assert "inferred" in lane_b.location


def test_weak_matches_become_not_covered(index_path: Path) -> None:
    out = ask(index_path, "What noise limits apply to drilling rigs at night near sampling wells?")
    assert out.status == "not covered"
    assert out.passages == []
    assert "don't appear to cover this" in out.explanation
    assert "None of the documents mention" in out.explanation


def test_unknown_terms_only_is_not_covered(index_path: Path) -> None:
    out = ask(index_path, "stamp duty conveyancing")
    assert out.status == "not covered"
    assert "none of the question's terms" in out.explanation


def test_other_state_rules_are_out_of_scope(index_path: Path) -> None:
    out = ask(index_path, "What does NSW require for groundwater sampling?")
    assert out.status == "not covered"
    assert "NSW" in out.explanation
    assert "Western Australian and national" in out.explanation


def test_a_named_document_that_is_not_indexed_is_said_plainly(index_path: Path) -> None:
    docs = (*DOCS[:3], _doc("nemp-3.1", "A", available=False))
    out = search_guidelines("What does NEMP 3.1 say about sampling?", index_path=index_path, manifest=docs)
    assert out.status == "not covered"
    assert "not indexed" in out.explanation
    assert "Synthetic: not indexed on purpose." in out.explanation


def test_value_questions_point_to_lookup_limit(index_path: Path) -> None:
    out = ask(index_path, "What is the drinking water guideline value?")
    assert NUMERIC_NOTE in out.notes
    assert "lookup_limit" in NUMERIC_NOTE


def test_no_searchable_words(index_path: Path) -> None:
    out = ask(index_path, "What is it?")
    assert out.status == "not covered"
    assert "no searchable words" in out.explanation


def test_k_limits_the_passages(index_path: Path) -> None:
    assert len(ask(index_path, "synthetic test text", k=2).passages) == 2


@pytest.mark.parametrize(
    ("question", "k", "message"),
    [("   ", 5, "empty"), ("x" * 501, 5, "limit is 500"), ("sampling", 0, "1 to 10"), ("sampling", 11, "1 to 10")],
)
def test_bad_input_is_explained(index_path: Path, question: str, k: int, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        ask(index_path, question, k)


def test_missing_index_is_explained(tmp_path: Path) -> None:
    with pytest.raises(EvidencelineError, match="has not been built"):
        search_guidelines("sampling", index_path=tmp_path / "absent.sqlite", manifest=DOCS)


def test_no_dashes_in_any_text_we_write(index_path: Path) -> None:
    for question in ("How are groundwater wells sampled?", "noise at night", "NSW sampling", "What is it?"):
        out = ask(index_path, question)
        text = " ".join([out.explanation, *out.notes, *(p.note + p.location for p in out.passages)])
        assert "\u2013" not in text
        assert "\u2014" not in text


# --- MCP tool ---------------------------------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def packaged_ids_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A synthetic index whose document ids match the packaged manifest, found through the cache variable."""
    chunks = [
        Chunk(
            "dwer-amcs",
            0,
            36,
            "31",
            "printed on the page",
            "9.2 Synthetic heading",
            f"{SYN} A sampling and analysis quality plan sets out sampling locations. {LONG_TAIL}",
        ),
    ]
    build_index(tmp_path / INDEX_NAME, [IndexedDocument("dwer-amcs", "0" * 64, 178, (), 1)], chunks)
    monkeypatch.setenv(CACHE_ENV, str(tmp_path))
    search_module._open_index.cache_clear()  # pyright: ignore[reportPrivateUsage]
    return tmp_path


def _structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    content: object = result.structured_content
    assert isinstance(content, dict)
    return cast(dict[str, Any], content)


@pytest.mark.anyio
@pytest.mark.usefixtures("packaged_ids_index")
async def test_strict_tool_over_mcp() -> None:
    server = MCPServer(name="guidance-test", tools=strict_tools())
    async with Client(server) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        tool = listed["search_guidelines"]
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
        assert tool.input_schema.get("additionalProperties") is False
        out = _structured(
            await client.call_tool("search_guidelines", {"question": "Does a SAQP set out sampling locations?"})
        )
        assert out["status"] == "passages found"
        assert out["passages"][0]["location"] == "p. 31 (PDF p. 36), 9.2 Synthetic heading"
        assert out["passages"][0]["licence_lane"] == "B"
        bad = await client.call_tool("search_guidelines", {"question": "sampling", "k": 50})
        assert bad.is_error
        block = bad.content[0]
        assert isinstance(block, TextContent)
        assert "1 to 10" in block.text
        typo = await client.call_tool("search_guidelines", {"question": "sampling", "kk": 2})
        assert typo.is_error


@pytest.mark.anyio
@pytest.mark.usefixtures("packaged_ids_index")
async def test_register_tools_adds_the_tool() -> None:
    server = MCPServer(name="guidance-register-test")
    register_tools(server)
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {"search_guidelines"}
        out = _structured(await client.call_tool("search_guidelines", {"question": "sampling quality plan", "k": 1}))
        assert len(out["passages"]) == 1


@pytest.mark.anyio
async def test_tool_reports_a_missing_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CACHE_ENV, str(tmp_path / "empty"))
    server = MCPServer(name="guidance-missing-test", tools=strict_tools())
    async with Client(server) as client:
        result = await client.call_tool("search_guidelines", {"question": "sampling"})
        assert result.is_error
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert "fetch_corpus.py" in block.text
