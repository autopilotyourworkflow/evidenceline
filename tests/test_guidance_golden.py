"""Golden-set checks against the real indexed corpus (evals/guidance_golden.json).

These tests need the public documents, which are never stored in the repository. They skip with a message when
the corpus has not been fetched and indexed:

    .venv/Scripts/python scripts/fetch_corpus.py
    .venv/Scripts/python scripts/build_index.py
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from evidenceline.guidance.evaluate import EvalReport, evaluate, load_golden
from evidenceline.guidance.scope import NUMERIC_NOTE
from evidenceline.guidance.search import EXCERPT_TOKENS, default_index_path, search_guidelines

MIN_HIT_AT_1 = Decimal("0.65")
MIN_HIT_AT_5 = Decimal("0.85")
MIN_RECALL_AT_8 = Decimal("0.72")
"""Floors a little under the rates measured on the whole golden file with passage search (2026-09-24, after the
casual-question changes: hit@1 0.74, hit@5 0.97, recall@8 0.79 over 35 in-scope questions), so a regression fails.
The same questions were used to tune the search, so these are not estimates of the rates on new questions."""

pytestmark = pytest.mark.skipif(
    not default_index_path().exists(),
    reason="Guidance corpus not fetched: run scripts/fetch_corpus.py then scripts/build_index.py to enable these.",
)


@pytest.fixture(scope="module")
def report() -> EvalReport:
    return evaluate(load_golden())


def test_every_expected_page_is_in_the_index() -> None:
    golden = load_golden()
    with sqlite3.connect(f"{default_index_path().resolve().as_uri()}?mode=ro", uri=True) as db:
        for item in golden.in_scope:
            for expected in item.expected:
                for pdf_page in expected.pdf_pages:
                    row = db.execute(
                        "SELECT 1 FROM chunks WHERE doc_id = ? AND pdf_page = ?", (expected.doc, pdf_page)
                    ).fetchone()
                    assert row is not None, f"{item.id}: {expected.doc} PDF p. {pdf_page} is not indexed"
                for section in expected.sections:
                    row = db.execute(
                        "SELECT 1 FROM chunks WHERE doc_id = ? AND section = ?", (expected.doc, section)
                    ).fetchone()
                    assert row is not None, f"{item.id}: {expected.doc} section {section!r} is not indexed"


def test_out_of_scope_questions_are_not_covered(report: EvalReport) -> None:
    assert report.oos_correct == report.out_of_scope, "\n".join(report.lines)


def test_hit_rates_hold(report: EvalReport) -> None:
    detail = report.summary() + "\n" + "\n".join(report.lines)
    assert report.rate(report.hit5, report.in_scope) >= MIN_HIT_AT_5, detail
    assert report.rate(report.hit1, report.in_scope) >= MIN_HIT_AT_1, detail
    assert report.mean_recall8 >= MIN_RECALL_AT_8, detail


@pytest.mark.parametrize(
    ("question", "doc", "pages"),
    [
        (
            "How should PFAS samples be stored and what containers should be used?",
            "nemp-3.0",
            {171, 172, 173, 226, 227, 228},
        ),
        ("What is the ecological soil value for PFOS?", "nemp-3.0", {63, 64, 65}),
        ("How many soil samples are needed for a site of one hectare?", "dwer-amcs", {38}),
        ("What is a conceptual site model?", "dwer-amcs", {27, 28}),
    ],
)
def test_practitioner_questions_open_on_the_right_page(question: str, doc: str, pages: set[int]) -> None:
    """Questions a practitioner asked while testing the search (they were also used to tune it)."""
    top = search_guidelines(question, 8).passages[0]
    assert top.document_id == doc, top.location
    assert top.pdf_page in pages, top.location


def test_value_questions_carry_the_lookup_limit_note(report: EvalReport) -> None:
    assert report.missing_numeric_note == []


def test_known_page_labels_on_the_real_documents() -> None:
    """Printed page numbers checked by hand against the PDFs (criteria research, 2026-09-23)."""
    with sqlite3.connect(f"{default_index_path().resolve().as_uri()}?mode=ro", uri=True) as db:

        def printed(doc: str, pdf_page: int) -> str | None:
            row = db.execute(
                "SELECT printed_page FROM chunks WHERE doc_id = ? AND pdf_page = ?", (doc, pdf_page)
            ).fetchone()
            return None if row is None else row[0]

        assert printed("nepm-b1", 56) == "48"  # Table 1A(1)
        assert printed("nemp-3.0", 57) == "48"  # Table 4
        assert printed("dwer-amcs", 8) == "3"  # superseded guidelines list


def test_passages_respect_licence_lengths_and_carry_sources() -> None:
    out = search_guidelines("What must a detailed site investigation report include?", 10)
    assert out.status == "passages found"
    for passage in out.passages:
        assert passage.excerpt_words <= EXCERPT_TOKENS[passage.licence_lane] + 1
        assert passage.edition
        assert passage.publication_date
        assert passage.notice
        assert passage.official_url
        assert passage.pdf_page is not None or passage.section is not None


def test_numeric_question_on_real_corpus_points_to_lookup_limit() -> None:
    out = search_guidelines("What is the drinking water guideline value for PFOS?")
    assert NUMERIC_NOTE in out.notes


def test_nemp_31_is_reported_as_not_indexed() -> None:
    out = search_guidelines("What does PFAS NEMP 3.1 say about soil reuse?")
    assert out.status == "not covered"
    assert "not indexed" in out.explanation
