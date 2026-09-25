"""The text a model reads for each passage: the whole indexed page, not just the short excerpt shown to people.

A search passage carries a licence-limited excerpt (25 to 60 words), too little to answer from. The model gets the
page it came from, looked up in the same SQLite index by document and page (and section, for web pages that are
split by heading). The page shown to people is still only the excerpt and the link; the answer is checked against
exactly the text the model was given.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evidenceline.guidance.manifest import allows_reuse
from evidenceline.guidance.models import Passage

MAX_CONTEXT_WORDS = 900
"""Longest page text given to the model per passage (pages are about 300 words; a few run longer)."""

_DASH_RANGE = re.compile(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)")
_DASH = re.compile(r"\s*[\u2013\u2014]\s*")


@dataclass(frozen=True, slots=True)
class PassageText:
    """One numbered passage as the model sees it: a header line and the page text.

    ``reusable`` is True when the passage's document allows reuse with attribution (CC BY, see
    :func:`evidenceline.guidance.manifest.allows_reuse`). It is never shown to the model; only the check on copied
    words reads it."""

    number: int
    header: str
    body: str
    reusable: bool = False

    @property
    def text(self) -> str:
        return f"{self.header}\n{self.body}"


def tidy_dashes(text: str) -> str:
    """En and em dashes become ' to ' between numbers and ', ' elsewhere, so the model does not copy them."""
    return _DASH.sub(", ", _DASH_RANGE.sub(" to ", text))


def _words(text: str) -> str:
    return " ".join(text.split())


def _best_fragment(excerpt: str) -> str:
    parts = [_words(part) for part in excerpt.replace("...", "\n").split("\n")]
    return max(parts, key=len, default="")


def _trim(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]) + " ..."


def _page_text(db: sqlite3.Connection, passage: Passage) -> str | None:
    """The chunk the passage came from: the only one on its page, else the one in its section, else the one that
    contains the longest stretch of its excerpt. None when that does not single out one chunk."""
    rows = db.execute(
        "SELECT section, text FROM chunks WHERE doc_id = ? AND pdf_page IS ?", (passage.document_id, passage.pdf_page)
    ).fetchall()
    chunks = [(None if row[0] is None else str(row[0]), str(row[1])) for row in rows]
    if len(chunks) == 1:
        return chunks[0][1]
    same_section = [text for section, text in chunks if section == passage.section]
    if len(same_section) == 1:
        return same_section[0]
    fragment = tidy_dashes(_best_fragment(passage.excerpt))
    candidates = same_section or [text for _, text in chunks]
    matching = [text for text in candidates if fragment and fragment in tidy_dashes(_words(text))]
    return matching[0] if len(matching) == 1 else None


def header(number: int, passage: Passage) -> str:
    """'[1] Title, edition. p. 216 (PDF p. 225), B.3.5 Quality control samples. Part of: Appendix B PFAS ambient
    sampling guideline > B.3 Sampling design.' The headings above the passage's own section are named, so the model
    can see when a page belongs to a narrower part of a document (an appendix for ambient sampling, a worked example)
    and say so instead of stating it as the general rule."""
    line = f"[{number}] {passage.document_title}, {passage.edition}. {passage.location}."
    path = passage.section_path or ""
    if " > " not in path:
        return line
    return f"{line} Part of: {path.rsplit(' > ', 1)[0]}."


def _page_texts(passages: Sequence[Passage], index_path: Path) -> list[str | None]:
    try:
        db = sqlite3.connect(f"{index_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return [None for _ in passages]
    try:
        return [_page_text(db, passage) for passage in passages]
    except sqlite3.Error:
        return [None for _ in passages]
    finally:
        db.close()


def passage_texts(passages: Sequence[Passage], index_path: Path) -> list[PassageText]:
    """The text for each passage, numbered from 1. Falls back to the excerpt when the page cannot be found."""
    texts: list[PassageText] = []
    for number, (passage, found) in enumerate(zip(passages, _page_texts(passages, index_path), strict=True), 1):
        body = _trim(_words(found), MAX_CONTEXT_WORDS) if found else passage.excerpt
        reusable = allows_reuse(passage.licence_lane, passage.licence)
        texts.append(PassageText(number, tidy_dashes(header(number, passage)), tidy_dashes(body), reusable))
    return texts
