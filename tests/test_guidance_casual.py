"""Casual, plain-language questions for 'Ask the guidelines': framing, everyday words, words no document uses, the
borderline band, and the question box's model call for a borderline "not covered".

The synthetic index holds text written for these tests (labelled so), not quotes from guidance documents. The tests
at the end use the real index and skip when the corpus has not been fetched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evidenceline.answer import FakeClient, answer
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.pipeline import BORDERLINE_INSTRUCTION
from evidenceline.answer.prompt import NOT_COVERED
from evidenceline.guidance.chunking import Chunk
from evidenceline.guidance.evaluate import Expected, Golden, GoldenQuestion, OutOfScopeQuestion, evaluate
from evidenceline.guidance.index import INDEX_NAME, IndexedDocument, build_index
from evidenceline.guidance.manifest import CorpusDocument, load_manifest
from evidenceline.guidance.models import GuidanceSearch, Passage
from evidenceline.guidance.scope import NUMERIC_NOTE, read_question
from evidenceline.guidance.search import default_index_path, search_guidelines
from evidenceline.guidance.synonyms import STOPWORDS, query_concepts, question_phrases

SYN = "Synthetic test text."
TAIL = " ".join(f"filler{i}" for i in range(40))


def labels(question: str) -> list[str]:
    return [c.label for c in query_concepts(question)]


# --- reading a casual question ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Can you tell me what a conceptual site model is?", ["conceptual site model"]),
        ("What's the deal with purging?", ["purging"]),
        ("I need to know the sampling density", ["number of samples"]),
        ("The foreman wants to know about purging", ["foreman", "purging"]),
        ("Where do I find the checklist?", ["checklist"]),
        ("What's the rule for eating eggs?", ["consumption", "poultry and eggs"]),
    ],
)
def test_question_framing_is_not_searched(question: str, expected: list[str]) -> None:
    assert labels(question) == expected


def test_contractions_and_conversation_words_are_not_searched() -> None:
    assert labels("Why hasn't Australia set a drinking water value for GenX?") == [
        "set",
        "drinking water",
        "value",
        "genx",
    ]
    assert labels("They don't agree, so we're asking again") == ["agree"]
    assert {"don", "hasn", "re", "asking", "ordinary", "enough", "apply"} <= STOPWORDS


@pytest.mark.parametrize(
    ("question", "group"),
    [
        ("What paperwork goes to DWER?", "forms and documentation"),
        ("Can you grow veg there?", "home-grown produce"),
        ("Can we reuse the dirt?", "soil"),
        ("Soil we dug up last week", "excavation"),
        ("Who pays for the clean-up?", "remediation"),
        ("Was the water tested?", "tested"),
        ("We truck the soil off site", "transport"),
        ("Leave the rain jacket at home", "clothing"),
        ("Bonded fibro fragments", "bonded asbestos"),
        ("Lead in the backyard", "garden"),
        ("Eggs from backyard chooks", "poultry and eggs"),
        ("Can kids play there?", "children"),
        ("An old petrol station", "service station"),
        ("The groundwater is used for watering livestock", "stock watering"),
        ("A neighbour's bore", "monitoring well"),
    ],
)
def test_everyday_words_map_to_the_documents_words(question: str, group: str) -> None:
    assert group in labels(question)


def test_a_one_word_synonym_still_pairs_with_its_neighbour() -> None:
    assert "contaminated soil" in question_phrases("Can PFAS contaminated soil be reused?")
    assert question_phrases("PFOS in potable water") == []


@pytest.mark.parametrize(
    ("question", "priced"),
    [
        ("any idea how much a PFAS lab test costs?", True),
        ("What would a PFAS clean-up cost?", True),
        ("How much does a lab charge per sample?", True),
        ("What does the cost-benefit analysis consider?", False),
        ("How much weight does cost carry when choosing a remediation option?", False),
        ("What does the guidance say about the cost of remediation options?", False),
    ],
)
def test_price_questions_are_off_topic_but_cost_as_a_factor_is_not(question: str, priced: bool) -> None:
    assert (read_question(question).off_topic is not None) is priced


# --- the search rules on a synthetic index --------------------------------------------------------------------------


def _doc(ident: str) -> CorpusDocument:
    template = next(d for d in load_manifest() if d.id == "nepm-b1")
    return replace(template, id=ident, title=f"Synthetic {ident}", short_title=ident, licence_lane="A")


DOCS = [_doc("syn-a")]


@pytest.fixture(scope="module")
def index_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("casual") / INDEX_NAME
    texts = [
        ("3.2 Sample containers", "Use polypropylene or HDPE bottles for every sample collected."),
        ("5 Mixing zones", "Mixing zones are not appropriate downstream of discharges."),
        ("7 Waste", "Waste tracking forms record the transport of material."),
        ("9 Laboratory", "Laboratory analysis methods report results."),
    ]
    chunks = [
        Chunk("syn-a", n, n + 1, str(n + 1), "printed on the page", section, f"{SYN} {text} {TAIL}")
        for n, (section, text) in enumerate(texts)
    ]
    build_index(path, [IndexedDocument("syn-a", "0" * 64, len(chunks), (), len(chunks))], chunks)
    return path


def ask(index_path: Path, question: str) -> GuidanceSearch:
    return search_guidelines(question, 5, index_path=index_path, manifest=DOCS)


def test_a_word_no_document_contains_adds_no_weight(index_path: Path) -> None:
    out = ask(index_path, "What bottles do samples go in on a job?")
    assert out.status == "passages found"
    top = out.passages[0]
    assert top.section == "3.2 Sample containers"
    assert top.coverage == "1.00"
    assert any("left them out: job" in note for note in out.notes)


def test_words_no_document_contains_still_count_in_the_more_than_half_rule(index_path: Path) -> None:
    out = ask(index_path, "Are mixing zones fine for trout?")
    assert out.status == "not covered"
    assert "no passage holds enough of what the question asks about" in out.explanation
    assert out.closest_passages == ()


def test_a_near_miss_carries_its_closest_passages_outside_the_tool_output(index_path: Path) -> None:
    out = ask(index_path, "Can we argue mixing zones downstream of discharges suit trout anglers?")
    assert out.status == "not covered"
    assert out.passages == []
    assert [p.section for p in out.closest_passages] == ["5 Mixing zones"]
    assert "closest" not in str(out.model_dump())
    assert "closest" not in str(GuidanceSearch.model_json_schema())


@pytest.mark.parametrize(
    "question",
    [
        "Can we argue mixing zones downstream of discharges suit Swanbourne Anglers?",  # names nothing indexed
        "Can we argue mixing zones downstream of discharges in Queensland?",  # another state
        "How much do mixing zones downstream of discharges cost?",  # a price
    ],
)
def test_plain_reason_not_covered_carries_no_passages(index_path: Path, question: str) -> None:
    out = ask(index_path, question)
    assert out.status == "not covered"
    assert out.closest_passages == ()


def test_the_evaluation_counts_borderline_passages_separately() -> None:
    near = _passage(1, "syn-a", 2, "5 Mixing zones")
    found = GuidanceSearch.model_construct(
        question="q", status="not covered", explanation="", passages=[], notes=[]
    ).with_closest_passages([near])
    golden = Golden(
        (GoldenQuestion("i1", "in", (Expected("syn-a", frozenset({2}), frozenset()),), False),),
        (OutOfScopeQuestion("o1", "out"),),
    )
    report = evaluate(golden, lambda question, k: found)
    assert (report.hit8, report.box_hit8) == (0, 1)
    assert (report.oos_correct, report.oos_to_model) == (1, 1)
    assert "question box: hit@8 counting borderline passages 1/1" in report.summary()
    assert "borderline" in report.lines[0]


# --- the question box: a borderline "not covered" goes to the model ------------------------------------------------


def _passage(rank: int, doc: str, page: int, section: str) -> Passage:
    fields: dict[str, Any] = {
        "rank": rank,
        "document_id": doc,
        "document_title": f"Title of {doc}",
        "edition": "Synthetic edition",
        "publication_date": "2021",
        "wa_status": "Synthetic status.",
        "pdf_page": page,
        "printed_page": str(page),
        "printed_page_basis": "printed on the page",
        "section": section,
        "section_path": section,
        "location": f"p. {page} (PDF p. {page}), {section}",
        "excerpt": "Mixing zones are not appropriate downstream of discharges.",
        "excerpt_words": 8,
        "licence_lane": "A",
        "licence": "Synthetic licence",
        "notice": "Notice.",
        "official_url": f"https://example.org/{doc}.pdf",
        "link": f"https://example.org/{doc}.pdf#page={page}",
        "matched": [],
        "coverage": "0.40",
        "note": "Note.",
    }
    return Passage.model_construct(**fields)


NEAR = [_passage(1, "syn-a", 2, "5 Mixing zones")]
ANSWER = "Mixing zones are not appropriate downstream of discharges [1]."


@pytest.fixture
def near_miss(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the search with a borderline "not covered" (closest passages attached) for every question, except a
    plain "not covered" with none for questions that mention trout."""
    seen: list[str] = []

    def fake_search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        seen.append(question)
        result = GuidanceSearch.model_construct(
            question=question,
            status="not covered",
            explanation="The indexed guidelines don't appear to cover this. No passage holds enough of the question.",
            passages=[],
            notes=[],
        )
        return result if "trout" in question else result.with_closest_passages(NEAR)

    monkeypatch.setattr(pipeline_module, "search_guidelines", fake_search)
    return seen


def test_a_borderline_question_is_answered_when_the_passages_answer_it(tmp_path: Path, near_miss: list[str]) -> None:
    client = FakeClient(reply=ANSWER)
    out = answer("Can we argue for a mixing zone downstream?", client, index_path=tmp_path / "none.sqlite")
    assert out.status == "answered"
    assert out.answer == ANSWER
    assert [c.cited for c in out.citations] == [True]
    assert "near miss" in out.explanation
    assert len(client.calls) == 1
    assert client.calls[0][1].endswith(BORDERLINE_INSTRUCTION)


def test_a_borderline_question_the_model_rejects_is_not_covered(tmp_path: Path, near_miss: list[str]) -> None:
    client = FakeClient(reply=NOT_COVERED)
    out = answer("Can we argue for a mixing zone downstream?", client, index_path=tmp_path / "none.sqlite")
    assert out.status == "not_covered"
    assert out.answer is None
    assert out.model == "fake-model"
    assert "near-miss passages" in out.explanation
    assert [c.section for c in out.citations] == ["5 Mixing zones"]
    assert len(client.calls) == 1


def test_a_plain_not_covered_never_calls_the_model(tmp_path: Path, near_miss: list[str]) -> None:
    client = FakeClient(reply=ANSWER)
    out = answer("Are mixing zones fine for trout?", client, index_path=tmp_path / "none.sqlite")
    assert out.status == "not_covered"
    assert out.citations == []
    assert client.calls == []


def test_a_borderline_verdict_question_never_calls_the_model(tmp_path: Path, near_miss: list[str]) -> None:
    client = FakeClient(reply=ANSWER)
    out = answer("Is the creek below the discharge contaminated?", client, index_path=tmp_path / "none.sqlite")
    assert out.status == "guard_rail"
    assert client.calls == []


def test_a_borderline_question_with_live_answers_off_stays_not_covered(tmp_path: Path, near_miss: list[str]) -> None:
    out = answer("Can we argue for a mixing zone downstream?", None, index_path=tmp_path / "none.sqlite")
    assert out.status == "not_covered"
    assert out.citations == []


# --- the real index ------------------------------------------------------------------------------------------------

real_index = pytest.mark.skipif(
    not default_index_path().exists(),
    reason="Guidance corpus not fetched: run scripts/fetch_corpus.py then scripts/build_index.py to enable these.",
)


@real_index
@pytest.mark.parametrize(
    ("question", "doc", "pages", "within"),
    [
        ("what paperwork do I need to give DWER when I find contamination?", "dwer-irc", {29, 30}, 3),
        ("can you grow veg on PFAS contaminated soil?", "nemp-3.0", {58}, 1),
        ("is there a limit for PFAS in drinking water?", "dwer-amcs", {165}, 3),
        ("what's a DSI?", "dwer-amcs", {34}, 1),
        ("how do I know if my site needs an auditor?", "dwer-amcs", {11, 12}, 2),
        ("what do I do if a neighbour's bore might be affected?", "dwer-amcs", {54}, 1),
    ],
)
def test_casual_questions_find_the_right_page(question: str, doc: str, pages: set[int], within: int) -> None:
    out = search_guidelines(question, 8)
    assert out.status == "passages found", out.explanation
    top = [(p.document_id, p.pdf_page) for p in out.passages[:within]]
    assert any(d == doc and page in pages for d, page in top), top


@real_index
def test_a_casual_value_question_still_points_to_lookup_limit() -> None:
    assert NUMERIC_NOTE in search_guidelines("is there a limit for PFAS in drinking water?", 8).notes


@real_index
def test_a_real_borderline_question_goes_to_the_model_once() -> None:
    client = FakeClient(reply=NOT_COVERED)
    out = answer("Can we argue for a mixing zone downstream of where the site discharges PFAS into the river?", client)
    assert out.status == "not_covered"
    assert len(client.calls) == 1
    assert client.calls[0][1].endswith(BORDERLINE_INSTRUCTION)
    assert out.citations


@real_index
@pytest.mark.parametrize(
    "question",
    [
        "What's the best way to cook kangaroo?",
        "What are the NSW rules for PFAS in soil?",
        "What PFAS concentrations were measured in groundwater at RAAF Base Pearce?",
        "What earthquake design loads apply to buildings in Perth?",
        "Which laboratory in Perth is cheapest for PFAS analysis?",
        "what's a good recipe for dinner tonight?",
        "any idea how much a PFAS lab test costs?",
    ],
)
def test_clear_off_topic_and_other_state_questions_never_reach_the_model(question: str) -> None:
    client = FakeClient(reply=ANSWER)
    out = answer(question, client)
    assert out.status == "not_covered", out.explanation
    assert client.calls == []
