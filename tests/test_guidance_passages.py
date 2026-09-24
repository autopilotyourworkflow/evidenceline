"""Passage chunking, heading detection, synonym triggers and ranking for 'Ask the guidelines'.

All text here is synthetic, written for the tests; none of it is quoted from a guidance document.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

from evidenceline.errors import EvidencelineError
from evidenceline.guidance.chunking import Chunk, chunk_markdown, chunk_pdf_pages, find_boilerplate
from evidenceline.guidance.evaluate import Expected, recall
from evidenceline.guidance.headings import (
    Heading,
    HeadingTracker,
    confirm_chapters,
    parse_heading,
    read_heading,
)
from evidenceline.guidance.index import INDEX_NAME, GuidanceIndex, Hit, IndexedDocument, build_index
from evidenceline.guidance.labels import derive_page_labels
from evidenceline.guidance.manifest import CorpusDocument, load_manifest
from evidenceline.guidance.models import Passage
from evidenceline.guidance.passages import MAX_WORDS, OVERLAP_WORDS, TARGET_WORDS, split_page, window_spans
from evidenceline.guidance.ranking import Scored, select
from evidenceline.guidance.search import search_guidelines
from evidenceline.guidance.synonyms import query_concepts, question_phrases

SYN = "Synthetic test text."


def words(count: int, stem: str = "word") -> str:
    return " ".join(f"{stem}{i}" for i in range(count))


# --- headings ---------------------------------------------------------------------------------------------------


def test_addresses_and_sentences_are_not_headings() -> None:
    assert parse_heading("8 Example Terrace") is None
    assert parse_heading("Table 3 below, together with other notes") is None
    caption = parse_heading("Table 6 Synthetic values for soil")
    assert caption == Heading("caption", "Table 6", "Synthetic values for soil")
    assert parse_heading("B.3.6 Collecting synthetic samples") == Heading(
        "numbered", "B.3.6", "Collecting synthetic samples"
    )


def test_a_heading_wrapped_over_three_lines_is_joined() -> None:
    lines = ["12 Reuse of synthetic", "material including soils and", "water", "Body text starts here."]
    heading, span = read_heading(lines, 0)
    assert heading is not None
    assert heading.text == "12 Reuse of synthetic material including soils and water"
    assert span == 3


def test_a_bare_appendix_line_takes_its_title_from_the_next_line_only_near_the_top() -> None:
    top = ["Appendix B", "Synthetic sampling guideline", "B.1 Objectives"]
    heading, span = read_heading(top, 0)
    assert heading is not None
    assert (heading.text, span) == ("Appendix B Synthetic sampling guideline", 2)
    low = ["line one.", "line two.", "line three.", "line four.", "Appendix D", "Synthetic cell"]
    assert read_heading(low, 4) == (None, 1)


def test_numbered_list_items_are_not_chapters() -> None:
    found = [
        Heading("numbered", "3", "Guiding principles"),
        Heading("numbered", "4", "Quantitative synthetic assessment"),
        Heading("numbered", "5", "Consistency across synthetic places"),
        Heading("numbered", "3.1", "General obligations"),
        Heading("numbered", "4", "Communication"),
        Heading("numbered", "4.1", "Roles"),
    ]
    assert confirm_chapters(found) == [True, False, False, True, True, True]
    tracker = HeadingTracker(confirm_chapters(found))
    accepted = [h.text for h in found if tracker.accept(h)]
    assert accepted == ["3 Guiding principles", "3.1 General obligations", "4 Communication", "4.1 Roles"]
    assert tracker.path == "4 Communication > 4.1 Roles"


def test_tracker_keeps_the_heading_path_and_rejects_out_of_order_numbers() -> None:
    tracker = HeadingTracker()
    for heading in [
        Heading("numbered", "1", "Introduction"),
        Heading("numbered", "2", "Sampling"),
        Heading("numbered", "2.2", "Quality control"),
        Heading("numbered", "2.2.1", "Blanks"),
    ]:
        assert tracker.accept(heading)
    assert tracker.path == "2 Sampling > 2.2 Quality control > 2.2.1 Blanks"
    assert not tracker.accept(Heading("numbered", "1.3", "Cross reference in a table"))
    assert not tracker.accept(Heading("numbered", "9", "A footnote far ahead"))
    assert tracker.section == "2.2.1 Blanks"


def test_appendix_numbering_and_rows_inside_an_appendix() -> None:
    tracker = HeadingTracker()
    assert tracker.accept(Heading("numbered", "1", "Introduction"))
    assert tracker.accept(Heading("appendix", "Appendix B", "Synthetic sampling guideline"))
    assert tracker.accept(Heading("numbered", "B.3", "Sampling design"))
    assert tracker.accept(Heading("numbered", "B.3.6", "Collecting samples"))
    assert tracker.path == "Appendix B Synthetic sampling guideline > B.3 Sampling design > B.3.6 Collecting samples"
    assert not tracker.accept(Heading("numbered", "2", "Checklist row"))
    assert not tracker.accept(Heading("numbered", "C.1", "Another appendix's number"))


def test_continued_captions_take_the_full_caption() -> None:
    tracker = HeadingTracker()
    assert tracker.caption(Heading("caption", "Table 5", "Synthetic soil levels")) == "Table 5 Synthetic soil levels"
    assert tracker.caption(Heading("caption", "Table 5", "continued")) == "Table 5 Synthetic soil levels"


def test_running_headers_with_section_and_appendix_names_are_one_key() -> None:
    topics = ["soil", "water", "air", "sediment", "biota", "waste", "foam", "reuse", "landfill", "wells"]
    pages = [f"Synthetic Plan 3.0 Section {n} {100 + n}\nThis page is about {topics[n]}." for n in range(7)]
    pages += [f"Synthetic Plan 3.0 Appendix {c} {200 + i}\nAbout {topics[7 + i]}." for i, c in enumerate("ABC")]
    assert find_boilerplate(pages) == {"synthetic plan #.# # # #"}


# --- passages ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 150, 300, 301, 520, 1000])
def test_windows_cover_every_word_within_the_size_limits(count: int) -> None:
    spans = window_spans(count)
    assert spans[0][0] == 0
    assert spans[-1][1] == count
    for (start, end), (next_start, _) in pairwise(spans):
        assert end - next_start == OVERLAP_WORDS
        assert end - start == TARGET_WORDS
    assert all(end - start <= MAX_WORDS for start, end in spans)
    if count > MAX_WORDS:
        assert spans[-1][1] - spans[-1][0] > MAX_WORDS - (TARGET_WORDS - OVERLAP_WORDS)


def test_a_page_is_split_at_headings_and_short_pieces_join_a_neighbour() -> None:
    lines = [
        "2.1 First synthetic section",
        words(80, "alpha"),
        "2.2 Second synthetic section",
        words(10, "beta"),
        "2.3 Third synthetic section",
        words(90, "gamma"),
    ]
    tracker = HeadingTracker()
    passages = split_page(lines, tracker)
    assert [p.section for p in passages] == ["2.1 First synthetic section", "2.3 Third synthetic section"]
    assert "beta0" in passages[1].text
    assert passages[1].section_path == "2.3 Third synthetic section"


def test_pdf_passages_never_cross_a_page_and_carry_the_heading_path() -> None:
    header = "Synthetic guideline for tests"
    pages = [
        f"{header} 1\n1 Synthetic sampling\n{words(40, 'intro')}\n1.1 Containers\n{words(700, 'bottle')}",
        f"{header} 2\n{words(120, 'more')}",
        f"{header} 3\nTable 2 Synthetic holding times\n{words(90, 'row')}",
        f"{header} 4\n{words(20, 'tail')}",
        f"{header} 5\n{words(20, 'end')}",
    ]
    chunks = chunk_pdf_pages("synthetic-doc", pages, derive_page_labels(pages))
    assert {c.pdf_page for c in chunks} == {1, 2, 3, 4, 5}
    assert all(c.word_count <= MAX_WORDS for c in chunks)
    first_page = [c for c in chunks if c.pdf_page == 1]
    assert len(first_page) >= 3
    assert first_page[-1].section_path == "1 Synthetic sampling > 1.1 Containers"
    assert all(header not in c.text for c in chunks)
    page_two = next(c for c in chunks if c.pdf_page == 2)
    assert page_two.section == "1.1 Containers"
    page_three = next(c for c in chunks if c.pdf_page == 3)
    assert page_three.captions == ("Table 2 Synthetic holding times",)


def test_long_markdown_paragraphs_are_windowed() -> None:
    text = f"# Synthetic page\n\n## Long section\n\n{words(700, 'para')}\n"
    chunks = chunk_markdown("synthetic-md", text)
    assert len(chunks) >= 3
    assert all(c.word_count <= MAX_WORDS and c.section == "Long section" for c in chunks)
    assert all(c.section_path == "Long section" for c in chunks)


# --- synonyms ---------------------------------------------------------------------------------------------------


def test_triggers_map_question_words_to_searchable_phrases() -> None:
    concepts = {c.label: c for c in query_concepts("How should PFAS samples be stored and what containers are used?")}
    assert set(concepts) == {"PFAS", "sample storage", "sample container"}
    assert "container" not in concepts["sample container"].phrases  # the stem would also match 'contains'
    assert "hdpe" in concepts["sample container"].phrases
    eco = [c.label for c in query_concepts("What is the ecological soil value for PFOS?")]
    assert eco == ["ecological investigation level", "PFOS"]
    assert [c.label for c in query_concepts("How many soil samples for one hectare?")] == [
        "number of samples",
        "site size",
    ]
    assert [c.label for c in query_concepts("When do I have to report it?")] == ["timeframe", "duty to report"]


def test_question_phrases_are_neighbouring_meaningful_words() -> None:
    assert question_phrases("What triggers a site assessment?") == ["site assessment"]
    assert question_phrases("PFOS in potable water") == []
    assert question_phrases("ambient background concentration") == ["ambient background", "background concentration"]
    assert question_phrases("conceptual site model") == []  # one synonym phrase, searched whole


# --- ranking ----------------------------------------------------------------------------------------------------


def _scored(rowid: int, page: int | None, relevance: str, section: str | None = None) -> Scored:
    hit = Hit(rowid, "doc", page, None, None, section, Decimal("-1"))
    return Scored(hit, Decimal(1), Decimal(0), Decimal(0), (), Decimal(relevance))


def test_select_keeps_one_passage_per_page_by_default() -> None:
    scored = [_scored(1, 5, "9"), _scored(2, 5, "8"), _scored(3, 6, "7"), _scored(4, None, "6", "Web section")]
    assert [s.hit.rowid for s in select(scored, 3)] == [1, 3, 4]
    assert [s.hit.rowid for s in select(scored, 3, max_per_page=2)] == [1, 2, 3]


# --- search on a synthetic index --------------------------------------------------------------------------------


def _doc(ident: str) -> CorpusDocument:
    template = next(d for d in load_manifest() if d.id == "nepm-b1")
    return replace(template, id=ident, title=f"Synthetic {ident}", short_title=ident, licence_lane="A")


@pytest.fixture(scope="module")
def index_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("passages") / INDEX_NAME
    chunks = [
        Chunk(
            "syn-a",
            0,
            4,
            "1",
            "printed on the page",
            "3.2 Sample containers",
            f"{SYN} Use polypropylene or HDPE bottles for every sample. {words(60, 'fill')}",
            section_path="3 Sampling > 3.2 Sample containers",
        ),
        Chunk(
            "syn-a",
            1,
            9,
            "6",
            "printed on the page",
            "5.1 Operations in Perth",
            f"{SYN} Contractors operate from Perth depots. {words(60, 'pad')}",
            section_path="5 Operations > 5.1 Operations in Perth",
        ),
        Chunk(
            "syn-a",
            2,
            10,
            "7",
            "printed on the page",
            "6 Treatment",
            f"{SYN} Treatment of water that contains synthetic substances. {words(60, 'more')}",
            section_path="6 Treatment",
            captions=("Table 9 Synthetic treatment options",),
        ),
    ]
    build_index(path, [IndexedDocument("syn-a", "0" * 64, 12, (), len(chunks))], chunks)
    return path


def test_containers_are_found_through_the_synonym_group(index_path: Path) -> None:
    out = search_guidelines("What containers should samples go in?", 3, index_path=index_path, manifest=[_doc("syn-a")])
    assert out.status == "passages found"
    top = out.passages[0]
    assert top.pdf_page == 4
    assert top.section_path == "3 Sampling > 3.2 Sample containers"
    assert "sample container" in top.matched


def test_two_rare_words_cannot_carry_a_passage(index_path: Path) -> None:
    """'contractors' and 'operate' are on one page, 'treatment' on another; 'weekends' is in no passage and still
    counts as part of the question, so two of four is not enough."""
    out = search_guidelines(
        "Which treatment contractors operate on weekends?", 3, index_path=index_path, manifest=[_doc("syn-a")]
    )
    assert out.status == "not covered"
    assert "no passage holds enough of what the question asks about" in out.explanation


def test_an_index_in_the_old_format_asks_for_a_rebuild(tmp_path: Path) -> None:
    path = tmp_path / INDEX_NAME
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES ('format_version', '1')")
    db.close()
    with pytest.raises(EvidencelineError, match="older format"):
        GuidanceIndex(path)


# --- evaluation -------------------------------------------------------------------------------------------------


def _passage(page: int | None, section: str | None = None, doc: str = "doc") -> Passage:
    return Passage(
        rank=1,
        document_id=doc,
        document_title="Synthetic",
        edition="Synthetic",
        publication_date="2026",
        wa_status="Synthetic",
        pdf_page=page,
        printed_page=None,
        printed_page_basis="not derived",
        section=section,
        location="synthetic",
        excerpt="synthetic",
        excerpt_words=1,
        licence_lane="A",
        licence="CC BY 4.0",
        notice="Synthetic",
        official_url="https://example.org",
        link="https://example.org",
        matched=[],
        coverage="1.00",
        note="Synthetic",
    )


def test_recall_counts_each_expected_page_once() -> None:
    expected = [Expected("doc", frozenset({1, 2, 3, 4}), frozenset()), Expected("web", frozenset(), frozenset({"S"}))]
    found = [_passage(1), _passage(1), _passage(3), _passage(None, "S", "web"), _passage(9)]
    assert recall(expected, found) == Decimal(3) / Decimal(5)
    assert recall(expected, []) == 0
