"""Split one page's lines into passages of about 150 to 300 words, each under the heading it belongs to.

A page is first cut at every accepted section heading, so a passage never mixes two sections unless one of them has
only a few words on the page. A piece longer than :data:`MAX_WORDS` is cut into overlapping windows of
:data:`TARGET_WORDS` words; the last window runs to the end of the piece, so no window is shorter than
``MAX_WORDS - (TARGET_WORDS - OVERLAP_WORDS)`` words. Passages never cross a page, so each one cites exactly one
PDF page and its printed page.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from evidenceline.guidance.headings import Heading, HeadingTracker, read_heading
from evidenceline.guidance.textclean import join_lines

TARGET_WORDS = 220
OVERLAP_WORDS = 60
MAX_WORDS = 300
MIN_PIECE_WORDS = 60
"""A piece of a page shorter than this (a heading and a few lines) joins its neighbour on the same page."""


@dataclass(frozen=True, slots=True)
class PagePassage:
    """One passage of a page: its text and where it sits in the document's headings."""

    section: str | None
    section_path: str | None
    captions: tuple[str, ...]
    text: str


@dataclass
class _Piece:
    section: str | None
    section_path: str | None
    lines: list[str] = field(default_factory=list[str])
    captions: list[str] = field(default_factory=list[str])

    @property
    def words(self) -> int:
        return sum(len(line.split()) for line in self.lines)


def window_spans(count: int) -> list[tuple[int, int]]:
    """(start, end) word positions of the windows over ``count`` words."""
    if count <= MAX_WORDS:
        return [(0, count)]
    spans: list[tuple[int, int]] = []
    start = 0
    while count - start > MAX_WORDS:
        spans.append((start, start + TARGET_WORDS))
        start += TARGET_WORDS - OVERLAP_WORDS
    spans.append((start, count))
    return spans


def scan_headings(lines: Sequence[str]) -> list[tuple[int, Heading | None, int]]:
    """(first line, heading or None, lines spanned) for the page, in reading order."""
    found: list[tuple[int, Heading | None, int]] = []
    index = 0
    while index < len(lines):
        heading, span = read_heading(lines, index)
        found.append((index, heading, span))
        index += span
    return found


def _pieces(lines: Sequence[str], tracker: HeadingTracker) -> list[_Piece]:
    pieces = [_Piece(tracker.section, tracker.path)]
    for index, heading, span in scan_headings(lines):
        if heading is not None and heading.kind == "caption":
            pieces[-1].captions.append(tracker.caption(heading))
        elif heading is not None and tracker.accept(heading):
            if pieces[-1].lines:
                pieces.append(_Piece(tracker.section, tracker.path))
            else:
                pieces[-1].section, pieces[-1].section_path = tracker.section, tracker.path
        pieces[-1].lines.extend(lines[index : index + span])
    return [piece for piece in pieces if piece.lines]


def _merge_small(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Groups of pieces that make one block each: a short piece joins the next one (or the previous, at the end)."""
    groups: list[list[_Piece]] = []
    pending: list[_Piece] = []
    for piece in pieces:
        pending.append(piece)
        if sum(p.words for p in pending) >= MIN_PIECE_WORDS:
            groups.append(pending)
            pending = []
    if pending:
        if groups:
            groups[-1].extend(pending)
        else:
            groups.append(pending)
    return groups


def split_page(lines: Sequence[str], tracker: HeadingTracker) -> list[PagePassage]:
    """Passages for one page, in reading order; ``tracker`` carries the headings from page to page."""
    passages: list[PagePassage] = []
    for group in _merge_small(_pieces(lines, tracker)):
        main = max(group, key=lambda piece: piece.words)
        captions = tuple(dict.fromkeys(c for piece in group for c in piece.captions))
        words = join_lines([line for piece in group for line in piece.lines]).split()
        for start, end in window_spans(len(words)):
            passages.append(PagePassage(main.section, main.section_path, captions, " ".join(words[start:end])))
    return passages
