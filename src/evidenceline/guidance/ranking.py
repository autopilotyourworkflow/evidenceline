"""Rank candidate passages for one question: BM25, then how much of the question each passage holds.

Every number here is a ``Decimal``; the only float is SQLite's BM25 value, converted on the way in.

For each passage the search knows, per concept of the question, whether the passage contains it and whether a
heading or caption above it names it. The final score is::

    relevance = -BM25 x coverage x (1 + HEADING_BONUS x heading share) x (1 + PHRASE_BONUS x phrase share)

- *coverage*: the IDF-weighted share of the question's concepts the passage contains (rare terms weigh more);
- *heading share*: the IDF-weighted share named in the passage's heading path or a table or figure caption, so the
  passage under '8.7.1 Ecological soil guideline values' beats a passage that only mentions the words in passing;
- *phrase share*: the share of the question's neighbouring word pairs ('site assessment') found side by side.

The constants were chosen on the golden set, and several settings scored within one question of each other (a
coverage power of 2 or 3, a heading bonus of 1 or 2, a phrase bonus of 1); the ones below sit in the middle of that
flat region. See :mod:`evidenceline.guidance.evaluate` for what each part is worth.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from evidenceline.guidance.index import Hit
from evidenceline.guidance.synonyms import Concept

HEADING_BONUS = Decimal("0.5")
PHRASE_BONUS = Decimal("0.5")
COVERAGE_POWER = 1
MAX_PER_PAGE = 1
"""At most this many passages from one PDF page (or one web page section) are returned; the next best passage from
another page takes the place of a second one. Overlapping windows of one page say the same thing twice."""
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class WeightedConcept:
    """One concept with its IDF weight and the passages that contain it (anywhere, and in a heading or caption)."""

    concept: Concept
    expression: str
    weight: Decimal
    rows: frozenset[int]
    heading_rows: frozenset[int]


@dataclass(frozen=True, slots=True)
class Scored:
    hit: Hit
    coverage: Decimal
    heading_share: Decimal
    phrase_share: Decimal
    matched: tuple[str, ...]
    relevance: Decimal
    """Larger is better."""


def idf(total: int, frequency: int) -> Decimal:
    """BM25's IDF, always positive: ln(1 + (N - n + 0.5) / (n + 0.5))."""
    half = Decimal("0.5")
    return (1 + (Decimal(total - frequency) + half) / (Decimal(frequency) + half)).ln()


def score(
    hit: Hit, concepts: Sequence[WeightedConcept], weight_sum: Decimal, phrases: Sequence[frozenset[int]]
) -> Scored:
    """Coverage, heading share, phrase share and relevance of one passage."""
    matched = [c for c in concepts if hit.rowid in c.rows]
    coverage = (sum((c.weight for c in matched), Decimal(0)) / weight_sum).quantize(_CENT)
    in_headings = sum((c.weight for c in matched if hit.rowid in c.heading_rows), Decimal(0))
    heading_share = (in_headings / weight_sum).quantize(_CENT)
    phrase_share = (
        (Decimal(sum(1 for rows in phrases if hit.rowid in rows)) / Decimal(len(phrases))).quantize(_CENT)
        if phrases
        else Decimal(0)
    )
    relevance = (
        -hit.score * coverage**COVERAGE_POWER * (1 + HEADING_BONUS * heading_share) * (1 + PHRASE_BONUS * phrase_share)
    )
    return Scored(hit, coverage, heading_share, phrase_share, tuple(c.concept.label for c in matched), relevance)


def page_key(hit: Hit) -> tuple[str, str]:
    """One PDF page, or one section of a web page."""
    return hit.doc_id, str(hit.pdf_page) if hit.pdf_page is not None else f"section:{hit.section}"


def select(scored: Sequence[Scored], k: int, max_per_page: int = MAX_PER_PAGE) -> list[Scored]:
    """The ``k`` most relevant passages, at most ``max_per_page`` from any one page. Ties keep index order."""
    chosen: list[Scored] = []
    per_page: dict[tuple[str, str], int] = {}
    for item in sorted(scored, key=lambda s: (-s.relevance, s.hit.rowid)):
        key = page_key(item.hit)
        if per_page.get(key, 0) >= max_per_page:
            continue
        per_page[key] = per_page.get(key, 0) + 1
        chosen.append(item)
        if len(chosen) == k:
            break
    return chosen
