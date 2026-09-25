"""The answer's shape since 25 September 2026: an easy first paragraph of 1 to 3 plain sentences, then, when the
passages give more, one or two detail paragraphs. Covers the "easy first paragraph" and "no run-on sentences" checks,
paragraph breaks (a single line break, a blank line, Windows line endings), the copied-words rule by licence (longer
runs only after the first paragraph and only from a CC BY document), and the accuracy checks, which apply to every
paragraph unchanged. A fake model and a fake search; the page text comes from a tiny SQLite file."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from evidenceline.answer import FakeClient, answer
from evidenceline.answer import pipeline as pipeline_module
from evidenceline.answer.clients import ModelReply
from evidenceline.answer.context import passage_texts
from evidenceline.answer.models import AnswerResult
from evidenceline.answer.pipeline import COPIED_NOTE, MAX_COPIED_PHRASES, copied_phrases
from evidenceline.answer.prompt import (
    LONG_SENTENCE_WORDS,
    MAX_EASY_SENTENCES,
    MAX_QUOTED_WORDS,
    MAX_REUSED_WORDS,
    MAX_SENTENCE_WORDS,
    REMINDER,
)
from evidenceline.answer.verify import paragraphs, sentences, tidy_paragraphs
from evidenceline.guidance.manifest import allows_reuse, load_manifest
from evidenceline.guidance.models import GuidanceSearch, Passage

QUESTION = "How many field duplicate samples should be collected?"
CC_BY_TEXT = (
    "Field duplicates are collected to check the precision of the sampling and analysis, and for waters one field "
    "duplicate should be collected for every 10 samples and sent to the primary laboratory, while one triplicate for "
    "every 20 samples should be sent to a second laboratory for comparison."
)
"""Page text of a passage from a CC BY document (PFAS NEMP 3.0 is one)."""
WA_TEXT = (
    "A detailed site investigation report includes the sampling rationale, the results for all monitoring wells and "
    "the conceptual site model, and it is prepared by a suitably qualified and experienced professional."
)
"""Page text of a passage from a document that allows only short excerpts (a DWER guideline)."""
REPORT_TEXT = "Known or suspected contaminated sites must be reported to DWER using the prescribed form."

EASY = (
    "Take one extra sample, called a duplicate, for every 10 water samples [1]. "
    "It shows whether sampling and testing give the same result twice [1]."
)
"""A good easy first paragraph: 2 sentences, each well under 30 words, in plain words."""
DETAIL = (
    "For waters, the guidance also sends one triplicate in every 20 samples to a second laboratory, so the two "
    "laboratories can be compared [1]."
)
"""A good detail paragraph: one cited sentence whose numbers are in the passage it cites."""


def _words(count: int, cite: str = "[1]") -> str:
    """A cited sentence of exactly ``count`` words (the citation is not a word). No numbers, nothing copied."""
    return " ".join(["Word", *["word"] * (count - 1)]) + f" {cite}."


def _copied(start: int, stop: int, text: str = CC_BY_TEXT, cite: str = "[1]") -> str:
    """A cited sentence that copies words ``start`` to ``stop`` of ``text`` word for word."""
    return "The guidance says " + " ".join(text.split()[start:stop]) + f" {cite}."


def _passage(rank: int, doc: str, page: int, excerpt: str, *, lane: str, licence: str) -> Passage:
    fields: dict[str, Any] = {
        "rank": rank,
        "document_id": doc,
        "document_title": f"Title of {doc}",
        "edition": "2025",
        "publication_date": "2025",
        "wa_status": "Status.",
        "pdf_page": page,
        "printed_page": str(page),
        "printed_page_basis": "printed on the page",
        "section": "1. Section",
        "section_path": "1. Section",
        "location": f"p. {page} (PDF p. {page}), 1. Section",
        "excerpt": excerpt,
        "excerpt_words": len(excerpt.split()),
        "licence_lane": lane,
        "licence": licence,
        "notice": "Notice.",
        "official_url": f"https://example.org/{doc}.pdf",
        "link": f"https://example.org/{doc}.pdf#page={page}",
        "matched": [],
        "coverage": "1.00",
        "note": "Note.",
    }
    return Passage.model_construct(**fields)


PASSAGES = [
    _passage(1, "nemp-3.0", 171, "Field duplicates are collected", lane="A", licence="CC BY 4.0"),
    _passage(2, "dwer-amcs", 34, "A detailed site investigation", lane="B", licence="Copyright Government of WA"),
    _passage(3, "dwer-irc", 30, "Known or suspected", lane="B", licence="Copyright Government of WA"),
]


@pytest.fixture
def index(tmp_path: Path) -> Path:
    """A tiny SQLite file with the chunks table the pipeline reads page text from."""
    path = tmp_path / "guidance.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id TEXT, pdf_page INTEGER, section TEXT, text TEXT)")
    db.executemany(
        "INSERT INTO chunks(doc_id, pdf_page, section, text) VALUES (?, ?, ?, ?)",
        [
            ("nemp-3.0", 171, "1. Section", CC_BY_TEXT),
            ("dwer-amcs", 34, "1. Section", WA_TEXT),
            ("dwer-irc", 30, "1. Section", REPORT_TEXT),
        ],
    )
    db.commit()
    db.close()
    return path


@pytest.fixture(autouse=True)
def searches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the search with one that always finds PASSAGES."""

    def fake_search(question: str, k: int = 5, *, index_path: Path | None = None) -> GuidanceSearch:
        return GuidanceSearch.model_construct(
            question=question, status="passages found", explanation="3 passages.", passages=PASSAGES, notes=[]
        )

    monkeypatch.setattr(pipeline_module, "search_guidelines", fake_search)


def _ask(reply: str, index: Path) -> AnswerResult:
    return answer(QUESTION, FakeClient(reply=reply), index_path=index)


def _failed(result: AnswerResult) -> list[str]:
    return [c.name for c in result.verification.checks if not c.passed]


def test_the_test_texts_are_what_they_claim() -> None:
    assert len(_words(45).split()) - 1 == 45
    assert all(len(s.split()) - 1 < MAX_SENTENCE_WORDS for s in sentences(EASY))
    assert (MAX_EASY_SENTENCES, MAX_SENTENCE_WORDS, LONG_SENTENCE_WORDS) == (3, 30, 60)
    assert (MAX_QUOTED_WORDS, MAX_REUSED_WORDS) == (10, 30)


# --- the easy first paragraph --------------------------------------------------------------------------------------


def test_an_easy_paragraph_then_a_detail_paragraph_is_answered(index: Path) -> None:
    out = _ask(f"{EASY}\n\n{DETAIL}", index)
    assert out.status == "answered", out.explanation
    assert out.answer == f"{EASY}\n\n{DETAIL}"
    checks = {c.name: c for c in out.verification.checks}
    assert checks["easy first paragraph"].detail == "The first paragraph has 2 sentence(s), each under 30 words."
    assert checks["no run-on sentences"].detail == "All 1 sentence(s) after the first paragraph have 60 words or fewer."


def test_an_answer_that_is_only_the_easy_paragraph_is_answered(index: Path) -> None:
    out = _ask(EASY, index)
    assert out.status == "answered", out.explanation
    run_on = next(c for c in out.verification.checks if c.name == "no run-on sentences")
    assert run_on.detail == "The answer has no paragraph after the first."


def test_one_sentence_is_enough_for_the_easy_paragraph(index: Path) -> None:
    out = _ask("Take one duplicate for every 10 water samples [1].", index)
    assert out.status == "answered", out.explanation


def test_four_sentences_in_the_first_paragraph_are_withheld(index: Path) -> None:
    four = " ".join([_words(8), _words(9), _words(10), _words(11)])
    out = _ask(four, index)
    assert out.status == "passages_only"
    assert out.answer is None
    assert _failed(out) == ["easy first paragraph"]
    detail = next(c.detail for c in out.verification.checks if c.name == "easy first paragraph")
    assert detail.startswith("the first paragraph has 4 sentences, and it may have at most 3.")
    assert "Open with an easy paragraph of 1 to 3 sentences" in detail
    assert "its first paragraph was not short and plain enough" in out.explanation


def test_three_sentences_in_the_first_paragraph_pass(index: Path) -> None:
    out = _ask(" ".join([_words(8), _words(9), _words(10)]), index)
    assert out.status == "answered", out.explanation


def test_a_31_word_sentence_in_the_first_paragraph_is_withheld(index: Path) -> None:
    out = _ask(f"{_words(31)}\n\n{DETAIL}", index)
    assert out.status == "passages_only"
    assert _failed(out) == ["easy first paragraph"]
    detail = next(c.detail for c in out.verification.checks if c.name == "easy first paragraph")
    assert detail.startswith("sentence 1, in the first paragraph, has 31 words.")


@pytest.mark.parametrize(("count", "passed"), [(29, True), (30, False)])
def test_the_first_paragraph_limit_is_under_30_words(index: Path, count: int, passed: bool) -> None:
    out = _ask(_words(count), index)
    assert (out.status == "answered") is passed


def test_a_45_word_sentence_in_the_second_paragraph_passes(index: Path) -> None:
    out = _ask(f"{EASY}\n\n{_words(45)}", index)
    assert out.status == "answered", out.explanation


@pytest.mark.parametrize(("count", "passed"), [(60, True), (61, False)])
def test_a_later_sentence_may_have_up_to_60_words(index: Path, count: int, passed: bool) -> None:
    out = _ask(f"{EASY}\n\n{DETAIL} {_words(count)}", index)
    assert (out.status == "answered") is passed, out.explanation
    if not passed:
        assert _failed(out) == ["no run-on sentences"]
        detail = next(c.detail for c in out.verification.checks if c.name == "no run-on sentences")
        assert detail == (
            "sentence 4 has 61 words, and a sentence in a detail paragraph may have at most 60 words: split it."
        )
        assert "a sentence was far too long" in out.explanation


def test_a_long_sentence_that_should_have_been_a_detail_paragraph_is_withheld(index: Path) -> None:
    """The same 45 words pass after a blank line, and fail when written as part of the first paragraph."""
    out = _ask(f"{EASY} {_words(45)}", index)
    assert _failed(out) == ["easy first paragraph"]


# --- paragraph breaks ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("separator", ["\n", "\n\n", "\r\n", "\r\n\r\n", "\n\n\n", " \n \n ", "\r"], ids=repr)
def test_every_paragraph_break_is_read_as_one(index: Path, separator: str) -> None:
    out = _ask(f"{EASY}{separator}{DETAIL}", index)
    assert out.status == "answered", out.explanation
    assert out.answer == f"{EASY}\n\n{DETAIL}", "stored with one blank line, where the website splits it"


def test_a_single_line_break_ends_the_easy_paragraph(index: Path) -> None:
    """Two sentences, a single line break, then two more: the first paragraph has 2 sentences, not 4."""
    out = _ask(f"{EASY}\n{DETAIL} {_words(12)}", index)
    assert out.status == "answered", out.explanation
    easy = next(c for c in out.verification.checks if c.name == "easy first paragraph")
    assert easy.detail.startswith("The first paragraph has 2 sentence(s)")


def test_the_website_splits_a_stored_answer_into_the_same_paragraphs(index: Path) -> None:
    out = _ask(f"{EASY}\r\n{DETAIL}\n\n\n{_words(12)}", index)
    assert out.answer is not None
    website = [p for p in re.split(r"\n\s*\n", out.answer) if p.strip()]  # AnswerCard.tsx, paragraphs()
    assert website == paragraphs(out.answer) == [EASY, DETAIL, _words(12)]


@pytest.mark.parametrize(
    ("text", "tidied"),
    [
        ("One [1].\nTwo [2].", "One [1].\n\nTwo [2]."),
        ("One [1].\r\n\r\nTwo [2].", "One [1].\n\nTwo [2]."),
        ("  One [1].   \n\n \n\t Two [2].  \n", "One [1].\n\nTwo [2]."),
        ("One [1]. Two [2].", "One [1]. Two [2]."),
        ("", ""),
    ],
)
def test_tidy_paragraphs(text: str, tidied: str) -> None:
    assert tidy_paragraphs(text) == tidied
    assert tidy_paragraphs(tidied) == tidied


def test_paragraphs_of_an_empty_answer() -> None:
    assert paragraphs("") == []
    assert paragraphs("  \n\n ") == []


def test_sentences_split_across_paragraph_breaks() -> None:
    for text in ("One here [1].\n\nTwo here [2].", "One here [1]\r\n\r\nTwo here [2]", "One here [1].\nTwo here [2]."):
        assert [s.rstrip(".") for s in sentences(text)] == ["One here [1]", "Two here [2]"]


# --- copied words, by licence --------------------------------------------------------------------------------------


def test_the_licence_flag_reaches_the_passage_text(index: Path) -> None:
    texts = passage_texts(PASSAGES, index)
    assert [t.reusable for t in texts] == [True, False, False]
    assert "CC BY" not in "".join(t.text for t in texts), "the model is not told the licences"


def test_a_long_run_from_a_cc_by_document_is_allowed_after_the_first_paragraph(index: Path) -> None:
    copied = _copied(0, 20)
    out = _ask(f"{EASY}\n\n{copied}", index)
    assert out.status == "answered", out.explanation


def test_a_long_run_from_a_cc_by_document_is_withheld_in_the_first_paragraph(index: Path) -> None:
    out = _ask(f"{_copied(0, 20)}\n\n{DETAIL}", index)
    assert out.status == "passages_only"
    assert _failed(out) == ["no long quotes"]
    detail = next(c.detail for c in out.verification.checks if c.name == "no long quotes")
    assert detail == (
        "sentence 1 copies more than 10 words in a row from passage 1: paraphrase it, since the first paragraph is "
        "always in your own words."
    )


def test_a_long_run_from_a_short_excerpt_document_is_always_withheld(index: Path) -> None:
    copied = _copied(0, 14, WA_TEXT, "[2]")
    for reply in (f"{EASY}\n\n{copied}", f"{copied}\n\n{DETAIL}"):
        out = _ask(reply, index)
        assert out.status == "passages_only", reply
        assert _failed(out) == ["no long quotes"]


def test_a_run_over_30_words_from_a_cc_by_document_is_withheld(index: Path) -> None:
    copied = _copied(0, 32)  # 32 words in a row, the numbers in them traced to passage 1
    out = _ask(f"{EASY}\n\n{copied}", index)
    assert out.status == "passages_only"
    assert _failed(out) == ["no long quotes"]
    detail = next(c.detail for c in out.verification.checks if c.name == "no long quotes")
    assert detail == "sentence 3 copies more than 30 words in a row from passage 1: paraphrase it."


def test_exactly_30_words_from_a_cc_by_document_are_allowed_after_the_first_paragraph(index: Path) -> None:
    out = _ask(f"{EASY}\n\n{_copied(0, 30)}", index)
    assert out.status == "answered", out.explanation


def test_ten_copied_words_are_allowed_anywhere(index: Path) -> None:
    out = _ask(f"{_copied(0, 10, WA_TEXT, '[2]')}\n\n{_copied(0, 10, WA_TEXT, '[2]')}", index)
    quotes = next(c for c in out.verification.checks if c.name == "no long quotes")
    assert quotes.passed, quotes.detail


def test_which_documents_allow_reuse_with_attribution() -> None:
    reusable = {doc.id for doc in load_manifest() if allows_reuse(doc.licence_lane, doc.licence)}
    assert reusable == {"nepm-b1", "nemp-3.0", "nemp-3.1"}


@pytest.mark.parametrize(
    ("lane", "licence", "allowed"),
    [
        ("A", "CC BY 4.0", True),
        ("A", "CC BY 3.0 AU", False),
        ("A", "CC BY-NC 4.0", False),
        ("A", "CC BY-ND 4.0", False),
        ("B", "CC BY 4.0", False),
        ("B", "Copyright Government of Western Australia (personal, non-commercial or internal use, unaltered)", False),
        ("C", "Copyright Commonwealth of Australia (NHMRC)", False),
    ],
)
def test_allows_reuse(lane: str, licence: str, allowed: bool) -> None:
    assert allows_reuse(lane, licence) is allowed


# --- the accuracy checks apply to every paragraph ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("later", "check"),
    [
        ("Duplicates go to the primary laboratory.", "every sentence cited"),
        ("Duplicate results are kept for 45 days [1].", "numbers traced"),
        ("A duplicate that matches means the water is fine to drink [1].", "no verdict wording"),
        ("A duplicate goes to the laboratory [4].", "citations exist"),
    ],
)
def test_an_accuracy_failure_in_the_second_paragraph_withholds_the_answer(index: Path, later: str, check: str) -> None:
    out = _ask(f"{EASY}\n\n{DETAIL} {later}", index)
    assert out.status == "passages_only"
    assert out.answer is None
    assert check in _failed(out)
    assert later not in out.model_dump_json(), "a withheld answer is never shown, not even in part"


def test_a_dash_in_the_second_paragraph_withholds_the_answer(index: Path) -> None:
    out = _ask(f"{EASY}\n\n{DETAIL.replace(', so', ' ' + chr(0x2014) + ' so')}", index)
    assert out.status == "passages_only"
    assert "no dashes" in _failed(out)


# --- the prompt's own example ---------------------------------------------------------------------------------------


def test_the_shape_the_reminder_suggests_passes_every_check(index: Path) -> None:
    shape = re.search(r"The shape is: '(.+?)' Then, only if it adds something: a blank line and '(.+?)'", REMINDER)
    assert shape is not None
    out = _ask(f"{shape.group(1)}\n\n{shape.group(2)}", index)
    assert out.status == "answered", out.explanation
    assert out.verification.passed is True


# --- review of the relaxed checks (25 September 2026) ---------------------------------------------------------------


@pytest.mark.parametrize("separator", ["\n\n", "\n", "\r\n", " \n\n "], ids=repr)
def test_a_citation_that_opens_a_paragraph_does_not_cite_the_paragraph_before(index: Path, separator: str) -> None:
    """The website shows each paragraph on its own, so a paragraph that ends with an uncited sentence is uncited even
    when the next paragraph starts with a citation."""
    reply = f"{EASY}\n\nDuplicates go to the primary laboratory.{separator}[1] {DETAIL}"
    out = _ask(reply, index)
    assert out.status == "passages_only", out.answer
    assert _failed(out) == ["every sentence cited"]
    detail = next(c.detail for c in out.verification.checks if c.name == "every sentence cited")
    assert detail == "sentence 3 of 4 has no citation."


def test_a_citation_on_its_own_line_does_not_cite_the_line_before(index: Path) -> None:
    out = _ask(f"Take one duplicate for every 10 water samples.\n[1]\n\n{DETAIL}", index)
    assert out.status == "passages_only", out.answer
    assert "every sentence cited" in _failed(out)


def test_sentences_keep_a_leading_citation_in_its_own_paragraph() -> None:
    assert sentences("One here. [1] Two here [2].") == ["One here. [1]", "Two here [2]."]
    assert sentences("One here.\n\n[1] Two here [2].") == ["One here.", "[1] Two here [2]."]
    assert sentences("One here.\n[1]") == ["One here.", "[1]"]


@pytest.mark.parametrize("first", ["[1]", "[1][2]", "[1].", "[1] [2]."])
def test_a_first_paragraph_of_citations_only_is_withheld(index: Path, first: str) -> None:
    """The easy first paragraph is what a reader sees first: it must hold words, not only citations."""
    out = _ask(f"{first}\n\n{EASY}\n\n{DETAIL}", index)
    assert out.status == "passages_only", out.answer
    assert _failed(out) == ["easy first paragraph"]
    detail = next(c.detail for c in out.verification.checks if c.name == "easy first paragraph")
    assert detail.startswith("the first paragraph has no words, only citations.")


# --- the second attempt after a copying failure quotes the passage's own words (25 September 2026) ------------------


class _Replies:
    """Gives each reply in turn and records every prompt."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []

    @property
    def model_id(self) -> str:
        return "fake-sequence"

    def complete(self, system: str, prompt: str) -> ModelReply:
        del system
        self.calls.append(prompt)
        return ModelReply(text=self.replies[min(len(self.calls), len(self.replies)) - 1], model="fake-sequence")


WA_RUN = "the sampling rationale, the results for all monitoring wells and the conceptual site model"
"""14 words of WA_TEXT exactly as the passage writes them, commas and all."""
COPIES_WA = (
    "Such a report covers THE SAMPLING RATIONALE the results for all monitoring wells and the conceptual site "
    "model [2]."
)
"""A draft sentence that copies WA_RUN in its own case and without the commas."""


def test_the_test_texts_for_the_retry_are_what_they_claim() -> None:
    assert WA_RUN in WA_TEXT
    assert WA_RUN not in COPIES_WA, "the draft writes the run differently from the passage"


def test_the_retry_after_a_copying_failure_quotes_the_passages_own_words(index: Path) -> None:
    client = _Replies(f"{EASY}\n\n{COPIES_WA}", f"{EASY}\n\n{DETAIL}")
    out = answer(QUESTION, client, index_path=index)
    assert out.status == "answered", out.explanation
    assert len(client.calls) == 2
    retry = client.calls[1]
    assert f'"{WA_RUN}" (passage 2)' in retry, "the passage's own words, from the passage text"
    assert COPIED_NOTE.split("{phrases}")[0] in retry
    assert COPIES_WA not in retry, "the retry never shows the model its own answer"
    assert "THE SAMPLING RATIONALE" not in retry


def test_copied_words_never_reach_the_check_record_or_the_explanation(index: Path) -> None:
    client = _Replies(f"{EASY}\n\n{COPIES_WA}")
    out = answer(QUESTION, client, index_path=index)
    assert out.status == "passages_only"
    assert _failed(out) == ["no long quotes"]
    assert WA_RUN in client.calls[1]
    shown = out.model_dump_json()
    for words in ("results for all monitoring wells", "RESULTS FOR ALL", "sampling rationale"):
        assert words.casefold() not in shown.casefold(), words
    detail = next(c.detail for c in out.verification.checks if c.name == "no long quotes")
    assert detail == "sentence 3 copies more than 10 words in a row from passage 2: paraphrase it."


def test_a_retry_for_another_failure_quotes_no_words(index: Path) -> None:
    client = _Replies(f"{EASY}\n\nDuplicates go to the primary laboratory.", f"{EASY}\n\n{DETAIL}")
    out = answer(QUESTION, client, index_path=index)
    assert out.status == "answered", out.explanation
    assert COPIED_NOTE.split("{phrases}")[0] not in client.calls[1]


def test_copied_phrases_follow_the_licence_rule(index: Path) -> None:
    texts = passage_texts(PASSAGES, index)
    cc_by_20 = " ".join(CC_BY_TEXT.split()[:20])
    assert copied_phrases(f"{EASY}\n\n{_copied(0, 20)}", texts) == [], "allowed after the first paragraph"
    assert copied_phrases(f"{_copied(0, 20)}\n\n{DETAIL}", texts) == [(1, cc_by_20)], "never in the first"
    over_30 = " ".join(CC_BY_TEXT.split()[:32]).rstrip(",")  # the run ends at a word, not at the comma after it
    assert copied_phrases(f"{EASY}\n\n{_copied(0, 32)}", texts) == [(1, over_30)]
    assert copied_phrases(f"{EASY}\n\n{_copied(0, 10, WA_TEXT, '[2]')}", texts) == [], "ten words are allowed"


def test_copied_phrases_are_the_whole_run_longest_first_and_capped(index: Path) -> None:
    texts = passage_texts(PASSAGES, index)
    reply = f"{_copied(2, 15, WA_TEXT, '[2]')} {_copied(0, 25, WA_TEXT, '[2]')}"
    found = copied_phrases(reply, texts)
    assert [number for number, _ in found] == [2, 2]
    assert found[0][1] == " ".join(WA_TEXT.split()[:25])
    assert found[1][1] == " ".join(WA_TEXT.split()[2:15])
    many = " ".join(_copied(k, k + 11, CC_BY_TEXT) for k in range(0, 36, 4))
    assert len(copied_phrases(many, texts)) == MAX_COPIED_PHRASES


def test_the_passage_header_names_the_headings_above_its_section() -> None:
    """A page from NEMP 3.0's ambient sampling appendix must not read as general groundwater practice: the header
    the model sees names the appendix above the passage's own section."""
    from evidenceline.answer.context import header
    from evidenceline.guidance.models import Passage

    def passage(path: str | None) -> Passage:
        return Passage.model_construct(
            document_title="PFAS National Environmental Management Plan Version 3.0",
            edition="Version 3.0",
            location="p. 216 (PDF p. 225), B.3.5 Quality control samples",
            section="B.3.5 Quality control samples",
            section_path=path,
        )

    nested = header(
        1, passage("Appendix B PFAS ambient sampling guideline > B.3 Sampling design > B.3.5 Quality control samples")
    )
    assert nested.endswith("Part of: Appendix B PFAS ambient sampling guideline > B.3 Sampling design.")
    for flat in ("B.3.5 Quality control samples", None, ""):
        assert "Part of" not in header(1, passage(flat))
