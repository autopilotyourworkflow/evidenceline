"""Typed output of the ``search_guidelines`` tool (published as its MCP output schema)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Passage(_Model):
    """One passage from an indexed guidance document, with everything needed to find it in the original."""

    rank: int = Field(description="1 is the best match.")
    document_id: str
    document_title: str
    edition: str = Field(description="Edition as printed, for example 'Version 3.0 (HEPA 2025)' or 'November 2021'.")
    publication_date: str = Field(description="Year, year-month or full date, as far as the document states it.")
    wa_status: str = Field(description="How the document stands in Western Australia, in one line.")
    pdf_page: int | None = Field(description="Page number in the PDF file (1 is the first page); null for web pages.")
    printed_page: str | None = Field(description="Page number printed on the page, or null when it was not derived.")
    printed_page_basis: str = Field(
        description="'declared in the PDF', 'printed on the page', 'inferred from neighbouring pages', "
        "'not derived', or 'web page (no pages)'."
    )
    section: str | None = Field(description="Nearest section heading detected, if any.")
    section_path: str | None = Field(
        default=None,
        description="The headings above the passage, outermost first, joined with ' > ', for example "
        "'18 PFAS sampling > 18.2 Sampling and quality assurance and quality control > 18.2.1 ...'.",
    )
    location: str = Field(description="One line a person can follow, for example 'p. 48 (PDF p. 57), 8.2 ...'.")
    excerpt: str = Field(description="A short extract of the passage (spacing and dash characters tidied).")
    excerpt_words: int
    licence_lane: Literal["A", "B", "C"]
    licence: str
    notice: str = Field(description="Attribution or copyright notice to keep with the excerpt.")
    official_url: str
    link: str = Field(description="Official URL, with '#page=N' for PDFs so most viewers open the right page.")
    matched: list[str] = Field(description="Question terms this passage contains.")
    coverage: str = Field(description="Share of the question's weighted terms the passage contains, 0 to 1.")
    note: str = Field(description="One line in plain English.")


class GuidanceSearch(_Model):
    """Result of a guidance search: passages with their sources, or an explicit 'not covered'."""

    question: str
    status: Literal["passages found", "not covered"]
    explanation: str = Field(description="What the result means, in plain English.")
    passages: list[Passage]
    notes: list[str]
    searched_terms: list[str] = Field(description="The terms searched for, after synonym expansion.")
    indexed_documents: list[str]
    unavailable_documents: list[str] = Field(description="Documents listed in the corpus but not indexed, and why.")

    _closest: tuple[Passage, ...] = PrivateAttr(default=())

    @property
    def closest_passages(self) -> tuple[Passage, ...]:
        """For a "not covered" result given only because no passage holds enough of the question: the closest
        passages when they are in the borderline band (``search.BORDERLINE_COVERAGE``), best first. Empty
        otherwise. Not a field: the tool output and its schema never show it; only the answer pipeline reads it."""
        return self._closest

    def with_closest_passages(self, passages: Sequence[Passage]) -> Self:
        """A copy of this result carrying ``passages`` as :attr:`closest_passages`."""
        copy = self.model_copy()
        copy._closest = tuple(passages)
        return copy
