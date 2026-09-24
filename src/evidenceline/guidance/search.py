"""'Ask the guidelines': find the passages in the indexed public guidance that answer a question.

Deterministic: SQLite FTS5 with BM25 ranking over passages of about 150 to 300 words, a hand-written synonym map, a
bonus for passages whose heading or caption names the question's terms and for the question's word pairs found
side by side (:mod:`evidenceline.guidance.ranking`), and an explicit "not covered" result when the best passage
covers too little of the question. No language model is called.

How a result is judged weak
    Each concept in the question (a word, or a synonym group such as "drinking water / potable") gets an IDF
    weight: rare terms weigh more than common ones. A passage's *coverage* is the weighted share of the question's
    known concepts (those some indexed passage contains) that it contains. A passage is shown when all three hold:

    - its coverage is at least :data:`MIN_COVERAGE`;
    - it holds more than half of the question's concepts, counting words no document contains (see below);
    - it holds at least :data:`MIN_MATCHED` concepts (or every known one, for a one-concept question), so a single
      rare word cannot carry a passage.

    If no passage passes, the answer is "not covered".

Words no indexed document contains
    An everyday word that no document uses ("argue", "job", "jacket") cannot be matched by any passage, so it adds
    no weight: it must not lower the coverage of the passage that answers the rest of the question. Before this
    rule such a word got the largest weight of all and pulled the best passage below the threshold. It still counts
    in the more-than-half rule, as part of what the question asks: "What earthquake design loads apply to buildings
    in Perth?" shares 'design', 'loads' and 'buildings' with the guidance, but not its subject. Two more rules keep
    "not covered" honest:

    - a word written as a name (an acronym, or capitalised where no sentence starts: "RAAF Base Pearce") also
      keeps its weight, the largest there is. A question about something the documents never name is not
      answered with passages that merely share "PFAS" and "groundwater";
    - when more than half of the question's concepts appear in no document ("what's a good recipe for dinner
      tonight?"), the answer is "not covered" without ranking anything.

    Everyday wordings the documents write differently ("paperwork", "veg", "dirt", "truck") are mapped to the
    documents' words in :mod:`evidenceline.guidance.synonyms`, and question framing ("can you tell me", "I need to
    know") is removed there, before any of this.

Borderline results
    When no passage passes but one comes close (coverage of at least :data:`BORDERLINE_COVERAGE`, at least
    :data:`BORDERLINE_MATCHED` of the question's concepts and at least half of the counted ones), the result is
    still "not covered" and carries the closest passages as
    :attr:`~evidenceline.guidance.models.GuidanceSearch.closest_passages`, which is not part of the tool output.
    Only the question box reads them: it gives them to the model, which must reply NOT_COVERED if they do not
    answer the question (:mod:`evidenceline.answer.pipeline`). A question naming something no document mentions,
    one below the band, and every plain-reason "not covered" below carry none.

Other plain-reason results, decided before any ranking and never borderline: a question about another state's or
country's rules (the corpus is WA and national guidance only), a question about prices, pay or suppliers, or one
that tries to instruct the system (:data:`evidenceline.guidance.scope.OFF_TOPIC`), a question that names a document
that is not indexed (PFAS NEMP 3.1), and a question with no searchable words.

Guideline values are never read from the passages: tables extract badly. A question that asks for a number gets
a note pointing to ``lookup_limit`` (verified PFAS drinking-water values) or to the cited page.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from functools import cache
from pathlib import Path

from evidenceline.errors import EvidencelineError
from evidenceline.guidance.index import HEADING_COLUMNS, INDEX_NAME, GuidanceIndex, Hit
from evidenceline.guidance.manifest import CorpusDocument, cache_dir, load_manifest
from evidenceline.guidance.models import GuidanceSearch, Passage
from evidenceline.guidance.ranking import Scored, WeightedConcept, idf, score, select
from evidenceline.guidance.scope import NUMERIC_NOTE, read_question
from evidenceline.guidance.synonyms import Concept, query_concepts, question_phrases

__all__ = [
    "BORDERLINE_COVERAGE",
    "BORDERLINE_MATCHED",
    "MIN_COVERAGE",
    "MIN_MATCHED",
    "NUMERIC_NOTE",
    "search_guidelines",
]

MIN_COVERAGE = Decimal("0.45")
"""A passage must contain at least this weighted share of the question's known concepts to be shown. Chosen on the
golden set; both evaluation sets score the same from 0.35 to 0.45 (see the evaluation module)."""
MIN_MATCHED = 2
"""A shown passage holds at least this many concepts of the question (every known one if it has fewer)."""
BORDERLINE_COVERAGE = Decimal("0.30")
BORDERLINE_MATCHED = 3
"""The borderline band. A question answered "not covered" for weak coverage carries its closest passages (as
``closest_passages``, for the question box's model to judge) when one of them has coverage of at least
BORDERLINE_COVERAGE, holds at least BORDERLINE_MATCHED of the question's concepts and at least half of the counted
ones, and the question names nothing the documents never mention. 0.30 is where coverage alone first let golden
off-topic questions through; three concepts and half keep out short off-topic questions that share two everyday
words with the guidance ("Can my dog swim in the Swan River?"). Measured in the evaluation module."""
CANDIDATES = 200
"""How many BM25 candidates are scored before the top k are chosen."""
MAX_K = 10
MAX_QUESTION_CHARS = 500
EXCERPT_TOKENS = {"A": 60, "B": 40, "C": 25}
"""Longest excerpt per licence lane: CC BY text (A) about 60 words; WA Government (B) and the stricter NHMRC
notice (C) shorter, always with the notice."""

EDITION_NOTE = (
    "Each passage shows its document's edition and WA status. Evidenceline does not decide which document or "
    "edition applies to a site; that is the scientist's call."
)
RESULTS_ARE_NOT_ADVICE = "These are search results, not an answer or advice."


@cache
def _open_index(path: Path) -> GuidanceIndex:
    return GuidanceIndex(path)


def default_index_path() -> Path:
    return cache_dir() / INDEX_NAME


def indexed_words(index_path: Path | None = None) -> Counter[str]:
    """Every word in the indexed passages with how often it appears (see :mod:`evidenceline.guidance.spelling`)."""
    return _open_index(index_path or default_index_path()).word_counts()


def _validate(question: str, k: int) -> str:
    text = " ".join(question.split())
    if not text:
        raise EvidencelineError(
            "The question is empty. Ask in plain English, for example 'What must a detailed site investigation "
            "report include?'."
        )
    if len(text) > MAX_QUESTION_CHARS:
        raise EvidencelineError(
            f"The question is {len(text)} characters long; the limit is {MAX_QUESTION_CHARS}. Ask one thing at a time."
        )
    if isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise EvidencelineError(f"k must be a whole number from 1 to {MAX_K}; got {k!r}.")
    return text


def _location(hit: Hit, doc: CorpusDocument) -> tuple[str, str]:
    """(one-line location, printed page basis)."""
    if doc.format != "pdf" or hit.pdf_page is None:
        where = f"section '{hit.section}' (web page, no page numbers)" if hit.section else "web page"
        return where, "web page (no pages)"
    section = f", {hit.section}" if hit.section else ""
    basis = hit.label_basis or "not derived"
    if hit.printed_page is None:
        return f"PDF p. {hit.pdf_page} (printed page number not derived){section}", basis
    inferred = " (printed page inferred)" if basis == "inferred from neighbouring pages" else ""
    return f"p. {hit.printed_page} (PDF p. {hit.pdf_page}){inferred}{section}", basis


def _passage_note(doc: CorpusDocument, basis: str) -> str:
    parts = ["Extracted text with spacing and dashes tidied; check the wording on the page before quoting it."]
    if basis == "inferred from neighbouring pages":
        parts.append("The printed page number is inferred from the pages either side.")
    elif basis == "not derived":
        parts.append("No printed page number could be read on this page; use the PDF page.")
    if doc.licence_lane != "A":
        parts.append("Short excerpt only because of the document's licence; read the rest at the official link.")
    return " ".join(parts)


def _trim_words(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]) + " ..."


def _name(doc: CorpusDocument) -> str:
    return f"{doc.short_title}, {doc.edition}"


class _Counts:
    """How many of the question's concepts a passage holds, against what the rules ask. Words no document contains
    count in the total but can never be matched."""

    def __init__(self, searchable: int, unknown: int) -> None:
        self.searchable = searchable
        """Concepts that some indexed passage contains."""
        self.total = searchable + unknown
        """Those plus the words no document contains: the base of the more-than-half rule."""

    def strong(self, item: Scored) -> bool:
        """Enough of the question in one passage: at least MIN_COVERAGE of the weighted terms, more than half of the
        counted concepts, and at least MIN_MATCHED of them (every searchable one, for a one-concept question). The
        count rules stop one or two rare words from carrying a passage that says nothing about the rest."""
        held = len(item.matched)
        return item.coverage >= MIN_COVERAGE and 2 * held > self.total and held >= min(MIN_MATCHED, self.searchable)

    def borderline(self, item: Scored) -> bool:
        """A near miss: coverage from BORDERLINE_COVERAGE up, at least BORDERLINE_MATCHED concepts, and at least
        half of the counted concepts (one short of the more-than-half rule for an even count)."""
        held = len(item.matched)
        return item.coverage >= BORDERLINE_COVERAGE and held >= BORDERLINE_MATCHED and 2 * held >= self.total


_SENTENCE_START = re.compile(r"(?:^|[.?!:;\"'(\[])\s*$")


def _written_as_name(word: str, text: str) -> bool:
    """Whether ``word`` appears in ``text`` written as a name: an acronym ('RAAF') or capitalised where no sentence
    starts ('Base Pearce'). Never for a question written mostly in capitals, where case says nothing."""
    letters = [c for c in text if c.isalpha()]
    if not letters or 2 * sum(c.isupper() for c in letters) > len(letters):
        return False
    for match in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, re.IGNORECASE):
        written = match.group(0)
        if not written[0].isalpha():
            continue
        if len(written) >= 2 and written.isupper():
            return True
        if written[0].isupper() and not _SENTENCE_START.search(text[: match.start()]):
            return True
    return False


class _Search:
    """One question against one index: holds the shared context so each step stays small."""

    def __init__(self, question: str, k: int, index: GuidanceIndex, docs: Sequence[CorpusDocument]) -> None:
        self.question = question
        self.k = k
        self.index = index
        self.docs = tuple(docs)
        indexed_ids = set(index.documents())
        self.indexed = [d for d in self.docs if d.id in indexed_ids]
        self.not_indexed = [d for d in self.docs if d.id not in indexed_ids]
        self.scope = read_question(question)
        self.notes = list(self.scope.notes)
        self.concepts: list[Concept] = query_concepts(self.scope.search_text)
        self.query = ""
        """The FTS5 expression searched (every searchable concept OR-ed), also used for excerpts."""

    def result(
        self, explanation: str, passages: list[Passage] | None = None, closest: Sequence[Passage] = ()
    ) -> GuidanceSearch:
        found = GuidanceSearch(
            question=self.question,
            status="passages found" if passages else "not covered",
            explanation=explanation,
            passages=passages or [],
            notes=self.notes,
            searched_terms=[c.label for c in self.concepts],
            indexed_documents=[_name(d) for d in self.indexed],
            unavailable_documents=[
                f"{_name(d)}: {d.availability_note if not d.available else 'not downloaded or indexed yet.'}"
                for d in self.not_indexed
            ],
        )
        return found.with_closest_passages(closest) if closest else found

    def run(self) -> GuidanceSearch:
        if not self.indexed:
            return self.result(
                "The search index holds none of the documents in the corpus manifest. Rebuild it with "
                "'python scripts/build_index.py'."
            )
        if self.scope.other_jurisdiction is not None:
            return self.result(
                "The indexed guidelines don't appear to cover this: the question is about "
                f"{self.scope.other_jurisdiction} rules, and the corpus holds only Western Australian and national "
                "guidance."
            )
        if self.scope.off_topic is not None:
            return self.result(f"The indexed guidelines don't appear to cover this: {self.scope.off_topic}.")
        doc_filter = self._document_filter()
        if isinstance(doc_filter, GuidanceSearch):
            return doc_filter
        if not self.concepts:
            return self.result(
                "The question has no searchable words beyond common words and document names. Say what you want "
                "to find, for example 'sampling requirements for groundwater'."
            )
        return self._search(doc_filter)

    def _document_filter(self) -> tuple[str, ...] | GuidanceSearch:
        named = self.scope.named_documents
        if not named:
            return ()
        by_id = {d.id: d for d in self.docs}
        searchable = tuple(sorted(d.id for d in self.indexed if d.id in named))
        missing = [by_id[i] for i in sorted(named) if i in by_id and i not in searchable]
        if not searchable:
            names = "; ".join(f"{_name(d)} ({d.availability_note or 'not indexed yet'})" for d in missing)
            return self.result(f"The question names a document that is not indexed: {names}")
        no_edition = self.scope.no_edition
        for doc in missing:
            said = (
                f"The question gives no edition, and {_name(doc)} is not indexed"
                if doc.id in no_edition
                else f"{_name(doc)} is named in the question but is not indexed"
            )
            self.notes.append(f"{said}, so only the other edition is searched. {doc.availability_note}")
        shown = ", ".join(_name(by_id[i]) for i in searchable)
        if no_edition.intersection(searchable):
            self.notes.append(
                f"Searched only what the question names: {shown}. It names a document without an edition, so every "
                "indexed edition of it is searched."
            )
        else:
            self.notes.append(f"Searched only the document named in the question: {shown}.")
        return searchable

    def _weigh(self) -> list[WeightedConcept]:
        total = self.index.chunk_count()
        weighted: list[WeightedConcept] = []
        for concept in self.concepts:
            expression = concept.fts_expression()
            rows = self.index.matching_rowids(expression)
            headings = self.index.matching_rowids(f"{HEADING_COLUMNS} : {expression}") if rows else frozenset[int]()
            weighted.append(WeightedConcept(concept, expression, idf(total, len(rows)), rows, headings))
        return weighted

    def _search(self, doc_filter: tuple[str, ...]) -> GuidanceSearch:
        weighted = self._weigh()
        missing = [c for c in weighted if not c.rows]
        live = [c for c in weighted if c.rows]
        absent = ", ".join(c.concept.label for c in missing)
        if not live:
            return self.result(
                f"The indexed guidelines don't appear to cover this: none of the question's terms ({absent}) appear "
                "in them."
            )
        if 2 * len(missing) > len(weighted):
            return self.result(
                "The indexed guidelines don't appear to cover this: most of the question's terms appear in none of "
                f"the indexed documents. None of the documents mention: {absent}."
            )
        names = [c for c in missing if _written_as_name(c.concept.label, self.scope.search_text)]
        everyday = [c.concept.label for c in missing if c not in names]
        counts = _Counts(len(live), len(missing))
        weight_sum = sum((c.weight for c in [*live, *names]), Decimal(0))
        self.query = " OR ".join(c.expression for c in live)
        phrases = [self.index.matching_rowids(f'"{pair}"') for pair in question_phrases(self.scope.search_text)]
        allowed = doc_filter or tuple(d.id for d in self.indexed)
        hits = self.index.search(self.query, CANDIDATES, allowed)
        scored = [score(hit, live, weight_sum, phrases) for hit in hits]
        kept = select([s for s in scored if counts.strong(s)], self.k)
        if not kept:
            best = max((s.coverage for s in scored), default=Decimal(0))
            near = [] if names else select([s for s in scored if counts.borderline(s)], self.k)
            mention = f" None of the documents mention: {absent}." if missing else ""
            return self.result(
                "The indexed guidelines don't appear to cover this. No passage holds enough of the question: the "
                f"best holds {best} of its weighted terms (at least {MIN_COVERAGE} and more than half of the terms "
                f"are needed).{mention}",
                closest=[self.passage(rank, item) for rank, item in enumerate(near, start=1)],
            )
        if names:
            self.notes.append(f"No indexed document mentions: {', '.join(c.concept.label for c in names)}.")
        if everyday:
            self.notes.append(
                f"No indexed document uses these words, so the search left them out: {', '.join(everyday)}."
            )
        self.notes.append(EDITION_NOTE)
        passages = [self.passage(rank, item) for rank, item in enumerate(kept, start=1)]
        plural = "s" if len(passages) != 1 else ""
        return self.result(
            f"{len(passages)} passage{plural} from the indexed guidance, best match first. Each gives the "
            "document, edition, page and a short excerpt; open the link to read it in context. "
            + RESULTS_ARE_NOT_ADVICE,
            passages,
        )

    def passage(self, rank: int, item: Scored) -> Passage:
        doc = next(d for d in self.indexed if d.id == item.hit.doc_id)
        limit = EXCERPT_TOKENS[doc.licence_lane]
        excerpt = _trim_words(self.index.snippet(item.hit.rowid, self.query, limit), limit)
        location, basis = _location(item.hit, doc)
        link = f"{doc.official_url}#page={item.hit.pdf_page}" if doc.format == "pdf" else doc.official_url
        return Passage(
            rank=rank,
            document_id=doc.id,
            document_title=doc.title,
            edition=doc.edition,
            publication_date=doc.publication_date,
            wa_status=doc.wa_status,
            pdf_page=item.hit.pdf_page,
            printed_page=item.hit.printed_page,
            printed_page_basis=basis,
            section=item.hit.section,
            section_path=item.hit.section_path,
            location=location,
            excerpt=excerpt,
            excerpt_words=len(excerpt.split()),
            licence_lane=doc.licence_lane,
            licence=doc.licence,
            notice=doc.notice,
            official_url=doc.official_url,
            link=link,
            matched=list(item.matched),
            coverage=str(item.coverage),
            note=_passage_note(doc, basis),
        )


def search_guidelines(
    question: str,
    k: int = 5,
    *,
    index_path: Path | None = None,
    manifest: Sequence[CorpusDocument] | None = None,
) -> GuidanceSearch:
    """Up to ``k`` passages from the indexed guidance that cover the question, or an explicit 'not covered'.

    Raises :class:`~evidenceline.errors.EvidencelineError` for an empty or overlong question, a bad ``k``, or an
    index that has not been built.
    """
    text = _validate(question, k)
    index = _open_index(index_path or default_index_path())
    docs = tuple(manifest) if manifest is not None else load_manifest()
    return _Search(text, k, index, docs).run()
