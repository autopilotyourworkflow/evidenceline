"""Unit tests for the guidance pipeline pieces: page labels, chunking, synonyms, question scope and the manifest.

All text here is synthetic, written for the tests; none of it is quoted from a guidance document.
"""

from __future__ import annotations

import json

import pytest

from evidenceline.errors import EvidencelineError
from evidenceline.guidance.chunking import (
    chunk_markdown,
    chunk_pdf_pages,
    clean_line,
    find_boilerplate,
    heading_of,
    is_contents_page,
    join_lines,
)
from evidenceline.guidance.labels import PageLabel, candidates_on_page, declared_labels_are_real, derive_page_labels
from evidenceline.guidance.manifest import load_manifest, parse_manifest
from evidenceline.guidance.scope import NUMERIC_NOTE, VERDICT_NOTE, read_question
from evidenceline.guidance.synonyms import query_concepts, singular, tokenize

HEADER = "Synthetic guideline for tests"


def page(number: int | None, body: str, *, header: str = HEADER) -> str:
    """A synthetic page: running header with the printed page number, then the body."""
    top = f"{header} {number}" if number is not None else header
    return f"{top}\n{body}\n"


# --- page labels -----------------------------------------------------------------------------------------------


def test_candidates_come_from_header_and_footer_lines() -> None:
    text = "Synthetic guideline 12\nbody line one\nbody line two\nbody three\nbody four\nbody five\nbody six\n12"
    assert candidates_on_page(text) == [12]
    assert candidates_on_page("Page 7 of 90\nsome text") == [7]


def test_printed_labels_follow_the_offset_after_front_matter() -> None:
    front = ["Cover page", "Copyright notice page", "Contents"]
    body = [page(n, f"Body text of printed page {n} with several words.") for n in range(1, 8)]
    labels = derive_page_labels(front + body)
    assert [label.label for label in labels] == [None, None, None, "1", "2", "3", "4", "5", "6", "7"]
    assert labels[0].basis == "not derived"
    assert labels[3].basis == "printed on the page"


def test_a_lone_number_that_disagrees_with_its_neighbours_is_not_a_page_number() -> None:
    body = [page(n, "Body text here for the page.") for n in range(1, 5)]
    divider = "Guiding\nprinciples\n3"  # a section divider whose '3' is a section number, not the page number
    tail = [page(n, "More body text for the page.") for n in range(6, 9)]
    labels = derive_page_labels([*body, divider, *tail])
    assert labels[4] == PageLabel("5", "inferred from neighbouring pages")
    assert [label.label for label in labels] == ["1", "2", "3", "4", "5", "6", "7", "8"]


def test_a_gap_between_different_offsets_is_left_not_derived() -> None:
    first = [page(n, "Body text") for n in range(1, 4)]
    unnumbered = "A landscape table with no footer\nrow one\nrow two"
    second = [page(n, "Body text") for n in range(20, 23)]
    labels = derive_page_labels([*first, unnumbered, *second])
    assert labels[3] == PageLabel(None, "not derived")


def test_declared_pdf_labels_win_only_when_they_are_real() -> None:
    assert not declared_labels_are_real(["1", "2", "3"])
    assert declared_labels_are_real(["A", "i", "1"])
    texts = ["cover", "front", "body"]
    assert [label.label for label in derive_page_labels(texts, ["A", "i", "1"])] == ["A", "i", "1"]
    assert derive_page_labels(texts, ["A", "i", "1"])[0].basis == "declared in the PDF"
    physical = derive_page_labels(texts, ["1", "2", "3"])
    assert all(label.basis == "not derived" for label in physical)


# --- chunking ---------------------------------------------------------------------------------------------------


def test_clean_line_tidies_spacing_bullets_and_dashes() -> None:
    assert clean_line("\u2022  Soil\u2002and\u00a0water") == "Soil and water"
    assert clean_line("Appendix A \u2013 Reporting checklists") == "Appendix A - Reporting checklists"
    assert clean_line("Table A1 \ufffd Checklist") == "Table A1 - Checklist"
    assert "\u2013" not in clean_line("0.5\u20131.0 m")
    assert "\u2014" not in clean_line("a\u2014b")


def test_join_lines_rejoins_hyphenated_words_only() -> None:
    assert join_lines(["the investi-", "gation level"]) == "the investigation level"
    assert join_lines(["PFOS-", "Contaminated"]) == "PFOS- Contaminated"
    assert join_lines(["one", "two"]) == "one two"


def test_headings_are_detected_but_contents_entries_and_list_items_are_not() -> None:
    assert heading_of("2.5 Ecological investigation levels") == "2.5 Ecological investigation levels"
    assert heading_of("Appendix B Potentially contaminating activities") is not None
    assert heading_of("1.1 Overview 1") is None  # contents entry ending with a page number
    assert heading_of("4. Calculate the value by adding the two numbers:") is None
    assert heading_of("Table 4 Health-based guideline values") == "Table 4 Health-based guideline values"
    assert heading_of("ordinary sentence in the body of the text") is None


def test_running_headers_are_found_and_dropped() -> None:
    topics = ["sampling", "wells", "soil", "vapour", "sediment", "biota", "waste", "reuse", "landfill", "foam"]
    pages = [page(n, f"This page is about {topic} and nothing else.") for n, topic in enumerate(topics, start=1)]
    boilerplate = find_boilerplate(pages)
    assert any("synthetic guideline for tests" in key for key in boilerplate)
    labels = derive_page_labels(pages)
    chunks = chunk_pdf_pages("synthetic-doc", pages, labels)
    assert len(chunks) == 10
    assert all(HEADER not in chunk.text for chunk in chunks)
    assert chunks[0].pdf_page == 1
    assert chunks[0].printed_page == "1"


def test_contents_pages_are_not_indexed_but_numeric_tables_are() -> None:
    contents = [f"{i}.{j} Section title words {i * 10 + j}" for i in range(1, 4) for j in range(1, 4)]
    assert is_contents_page(contents)
    table = [f"{ph} 15 20 25 30 40 {50 + k * 10}" for k, ph in enumerate(["4.0", "4.5", "5.0", "5.5", "6.0"] * 2)]
    assert not is_contents_page(table)
    data_rows = [f"Chemical{k} {v}" for k, v in enumerate([300, 1200, 600, 1500, 30, 4000, 70, 90, 20])]
    assert not is_contents_page(data_rows)


def test_section_heading_is_carried_to_following_pages() -> None:
    pages = [
        page(1, "3.1 Sampling design\nWords about the sampling design for groundwater wells."),
        page(2, "More words on the same topic continue on this page."),
        page(3, "3.2 Data evaluation\nWords about evaluating the data."),
    ]
    chunks = chunk_pdf_pages("synthetic-doc", pages, derive_page_labels(pages))
    assert [c.section for c in chunks] == ["3.1 Sampling design", "3.1 Sampling design", "3.2 Data evaluation"]


def test_reference_lists_are_back_matter_until_the_next_appendix() -> None:
    pages = [
        page(1, "5 Findings\nBody words of the last numbered section in the document."),
        page(2, "References\nAuthor A (2020) Title of a report, Publisher, City."),
        page(3, "Author B (2021) Another report title, Publisher, City."),
        page(4, "Appendix A Checklist\nItems a report should contain, listed here."),
    ]
    chunks = chunk_pdf_pages("synthetic-doc", pages, derive_page_labels(pages))
    assert [c.pdf_page for c in chunks] == [1, 4]


def test_markdown_is_chunked_by_heading_path_and_skips_references() -> None:
    text = (
        "> site banner line\n\n# Synthetic substance\n\nIntro words for the synthetic fact sheet here.\n\n"
        "## Guideline\n\n***Based on synthetic considerations, a synthetic value applies.***\\\n\n"
        "## Health considerations\n\n### *Substance X*\n\nWords about substance X health studies here.\n\n"
        "## References\n\nAuthor (2020). A reference entry that should not be indexed at all.\n"
    )
    chunks = chunk_markdown("synthetic-md", text)
    assert [c.section for c in chunks] == ["Synthetic substance", "Guideline", "Health considerations > Substance X"]
    assert all(c.pdf_page is None and c.printed_page is None for c in chunks)
    assert "***" not in chunks[1].text
    assert "\\" not in chunks[1].text


# --- synonyms ---------------------------------------------------------------------------------------------------


def test_synonym_groups_expand_domain_terms() -> None:
    concepts = {c.label: c for c in query_concepts("PFOS in potable water")}
    assert set(concepts) == {"PFOS", "drinking water"}
    assert "perfluorooctane sulfonate" in concepts["PFOS"].phrases
    assert '"drinking water"' in concepts["drinking water"].fts_expression()
    assert '"potable"' in concepts["drinking water"].fts_expression()


def test_long_names_and_plurals_match_the_group() -> None:
    labels = [c.label for c in query_concepts("perfluorooctane sulfonic acid investigation levels")]
    assert labels == ["PFOS", "guideline value"]
    assert singular("levels") == "level"
    assert singular("process") == "process"
    assert singular("gas") == "gas"


def test_stopwords_and_single_characters_are_dropped() -> None:
    assert [c.label for c in query_concepts("What is the value of a TDI?")] == ["value", "tolerable daily intake"]
    assert query_concepts("what is the") == []
    assert tokenize("5 \u00b5g/L") == ["5", "ug", "l"]


def test_fts_expressions_quote_every_phrase() -> None:
    concept = query_concepts('sampling "OR" NEAR')[0]
    assert concept.fts_expression() == '"sampling"'
    assert all(c.fts_expression().count('"') % 2 == 0 for c in query_concepts('a "quoted" (odd) query*'))


# --- question scope ---------------------------------------------------------------------------------------------


def test_value_questions_get_the_lookup_limit_note() -> None:
    assert NUMERIC_NOTE in read_question("What is the drinking water guideline for PFOS?").notes
    assert NUMERIC_NOTE in read_question("What concentration of PFOA triggers investigation?").notes
    assert NUMERIC_NOTE not in read_question("What is the limit of reporting for PFOS?").notes
    assert NUMERIC_NOTE not in read_question("Who must report a contaminated site?").notes


def test_verdict_questions_are_answered_with_the_investigation_level_caveat() -> None:
    assert VERDICT_NOTE in read_question("Is my bore water safe to drink?").notes
    assert VERDICT_NOTE in read_question("Is the site contaminated?").notes


def test_other_jurisdictions_are_detected_unless_wa_or_national_is_named() -> None:
    assert read_question("What does NSW require for sampling?").other_jurisdiction == "NSW"
    assert read_question("Does the NEMP apply in New Zealand?").other_jurisdiction is None
    assert read_question("Rules for a site in Victoria Park?").other_jurisdiction is None


def test_named_documents_limit_the_search_and_are_removed_from_the_words() -> None:
    scope = read_question("What does PFAS NEMP 3.1 say about soil reuse?")
    assert scope.named_documents == {"nemp-3.1"}
    assert "NEMP" not in scope.search_text
    assert "3.1" not in scope.search_text
    assert read_question("NEMP 3.0 drinking water").named_documents == {"nemp-3.0"}
    assert read_question("What does the NEMP say?").named_documents == {"nemp-3.0", "nemp-3.1"}
    assert read_question("ASC NEPM Schedule B1 lead").named_documents == {"nepm-b1"}


# --- manifest ---------------------------------------------------------------------------------------------------


def test_packaged_manifest_is_valid_and_honest_about_availability() -> None:
    docs = {d.id: d for d in load_manifest()}
    assert 4 <= len(docs) <= 6
    assert not docs["nemp-3.1"].available
    assert docs["nemp-3.1"].availability_note
    for doc in docs.values():
        assert doc.licence_lane in ("A", "B", "C")
        assert doc.notice
        assert doc.official_url.startswith("https://")
        assert "\u2013" not in doc.notice + doc.wa_status
        assert "\u2014" not in doc.notice + doc.wa_status


def test_manifest_rejects_bad_entries() -> None:
    good = json.loads(json.dumps({"documents": [_raw_doc("a")]}))
    assert parse_manifest(json.dumps(good))[0].id == "a"
    with pytest.raises(EvidencelineError, match="duplicate"):
        parse_manifest(json.dumps({"documents": [_raw_doc("a"), _raw_doc("a")]}))
    with pytest.raises(EvidencelineError, match="licence lane"):
        parse_manifest(json.dumps({"documents": [{**_raw_doc("a"), "licence_lane": "D"}]}))
    with pytest.raises(EvidencelineError, match="fetch_urls"):
        parse_manifest(json.dumps({"documents": [{**_raw_doc("a"), "fetch_urls": []}]}))


def _raw_doc(ident: str) -> dict[str, object]:
    return {
        "id": ident,
        "title": "Synthetic document",
        "short_title": "Synthetic",
        "issuer": "Test",
        "edition": "Test edition",
        "publication_date": "2026",
        "wa_status": "Synthetic, for tests.",
        "format": "pdf",
        "official_url": "https://example.org/doc.pdf",
        "fetch_urls": ["https://example.org/doc.pdf"],
        "licence_lane": "A",
        "licence": "CC BY 4.0",
        "notice": "Synthetic notice.",
        "available": True,
        "availability_note": "",
        "sha256": None,
        "retrieved": None,
    }
