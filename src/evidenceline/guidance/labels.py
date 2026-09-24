"""Printed page numbers: work out the number printed on each PDF page, or say that it could not be derived.

A citation needs both numbers. "p. 48" is what a scientist looks for in the document; "PDF p. 57" is where the PDF
viewer opens. The two differ by the cover, contents and front matter, and the difference can change part-way through
a document. Three sources are used, in this order:

1. **Labels declared in the PDF** (``/PageLabels``), when the publisher set real ones. A PDF with no labels makes
   pypdf report the physical page numbers ("1", "2", ...), which say nothing, so those are ignored.
2. **Numbers printed on the page**: a number standing alone, or at the end or start of a running header or footer
   line, in the first or last few lines of the page text. A candidate is accepted only when a nearby page has a
   candidate with the same offset from the PDF page number, so a stray number in a table or a section divider
   ("Guiding principles 3") is not mistaken for a page number.
3. **Neighbouring pages**: a page with no printed number that sits between two accepted pages with the same
   offset gets the number that offset implies, marked as inferred. Equal offsets on both sides mean the pages in
   between were counted in the numbering (a landscape table whose footer the text layer lost, for example). The
   run of unnumbered pages is capped at :data:`MAX_GAP`.

Anything else is "not derived". Nothing is guessed beyond that.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

LabelBasis = Literal["declared in the PDF", "printed on the page", "inferred from neighbouring pages", "not derived"]

EDGE_LINES = 3
"""How many non-empty lines at the top and at the bottom of a page are searched for a page number."""
NEIGHBOUR_WINDOW = 3
"""A candidate needs a page within this many pages with the same offset to be accepted."""
MAX_GAP = 40
"""Longest run of unnumbered pages that may be filled in from the pages on either side."""

_ALONE = re.compile(r"^\s*(?:page\s+)?(\d{1,4})\s*$", re.IGNORECASE)
_TRAILING = re.compile(r"^\D.*?\D\s(\d{1,4})\s*$")
_LEADING = re.compile(r"^\s*(\d{1,4})\s{1,}\D")
_PAGE_OF = re.compile(r"\bpage\s+(\d{1,4})\s+of\s+\d{1,4}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PageLabel:
    """The printed page number of one PDF page, and how it was worked out."""

    label: str | None
    basis: LabelBasis


def declared_labels_are_real(labels: Sequence[str]) -> bool:
    """True when the PDF declares page labels other than the physical page numbers."""
    return any(label != str(index + 1) for index, label in enumerate(labels))


def candidates_on_page(text: str) -> list[int]:
    """Numbers that could be the printed page number: found in the first and last few non-empty lines."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    edges = lines[:EDGE_LINES] + lines[-EDGE_LINES:] if len(lines) > 2 * EDGE_LINES else lines
    found: list[int] = []
    for line in edges:
        for pattern in (_PAGE_OF, _ALONE, _TRAILING, _LEADING):
            match = pattern.search(line)
            if match:
                number = int(match.group(1))
                if number not in found:
                    found.append(number)
                break
    return found


def derive_page_labels(page_texts: Sequence[str], declared: Sequence[str] | None = None) -> list[PageLabel]:
    """One :class:`PageLabel` per page. ``declared`` is pypdf's ``page_labels`` list, if any."""
    count = len(page_texts)
    if declared is not None and len(declared) == count and declared_labels_are_real(declared):
        return [PageLabel(label, "declared in the PDF") for label in declared]

    offsets: list[set[int]] = []
    for index, text in enumerate(page_texts):
        offsets.append({number - (index + 1) for number in candidates_on_page(text)})

    accepted: list[int | None] = [None] * count
    for index in range(count):
        low, high = max(0, index - NEIGHBOUR_WINDOW), min(count, index + NEIGHBOUR_WINDOW + 1)
        neighbour_offsets: set[int] = set()
        for other in range(low, high):
            if other != index:
                neighbour_offsets |= offsets[other]
        shared = offsets[index] & neighbour_offsets
        if shared:
            # Prefer the offset closest to zero when two could fit (rare; keeps the choice deterministic).
            accepted[index] = min(shared, key=lambda offset: (abs(offset), offset))

    labels: list[PageLabel] = []
    for index in range(count):
        offset = accepted[index]
        if offset is not None and index + 1 + offset >= 1:
            labels.append(PageLabel(str(index + 1 + offset), "printed on the page"))
            continue
        inferred = _infer_from_neighbours(accepted, index)
        if inferred is not None and index + 1 + inferred >= 1:
            labels.append(PageLabel(str(index + 1 + inferred), "inferred from neighbouring pages"))
        else:
            labels.append(PageLabel(None, "not derived"))
    return labels


def _infer_from_neighbours(accepted: Sequence[int | None], index: int) -> int | None:
    earlier = range(index - 1, max(-1, index - MAX_GAP - 2), -1)
    later = range(index + 1, min(len(accepted), index + MAX_GAP + 2))
    before = next((i for i in earlier if accepted[i] is not None), None)
    after = next((i for i in later if accepted[i] is not None), None)
    if before is None or after is None or after - before - 1 > MAX_GAP:
        return None
    if accepted[before] == accepted[after]:
        return accepted[before]
    return None
