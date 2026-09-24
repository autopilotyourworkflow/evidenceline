"""Section headings in extracted guidance pages: which lines are headings, and which heading a passage sits under.

A heading is a numbered line ('8.7.1 Ecological soil guideline values'), a lettered appendix subsection ('B.3.6
Collecting samples') or an appendix title ('Appendix B PFAS ambient sampling guideline'). Table and figure captions
('Table 6 Ecological guideline values for soil') are recognised separately: they label a passage but do not start a
section.

Numbered lines are only accepted when their numbering fits the document so far (:class:`HeadingTracker`). This keeps
out the usual false headings in extracted PDF text: a street address on a cover page ('8 Davidson Terrace'), a
footnote ('1 For example, ...'), a row of a data table ('11 PFCAs') and a cross-reference cell in a table ('5.2
Ambient monitoring programs' printed inside section 18). A heading wrapped over two lines is joined back together.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MAX_TITLE_WORDS = 12
MAX_CONTINUATION_WORDS = 6
"""A short lower-case line straight after a heading is the rest of a heading wrapped onto the next line."""
MAX_HEADING_LINES = 3
TOP_LINES_FOR_APPENDIX = 4
"""An appendix heading starts a page; an 'Appendix B' line lower down is a cross-reference or a table cell."""
MAX_FIRST_CHAPTER = 3
"""Before any chapter is seen, a one-number heading above this is not trusted (a cover-page street number)."""
MAX_CHAPTER_STEP = 2
"""A new chapter number may skip at most one chapter the extraction missed."""

_NUMBER = r"(?:\d{1,2}\.){0,3}\d{1,2}\.?|[A-H]\.\d{1,2}(?:\.\d{1,2}){0,2}|Section \d{1,2}"
_TITLE = r"[A-Z][A-Za-z0-9 ,:;'()/&+-]{2,90}"
_NUMBERED = re.compile(rf"^(?P<num>{_NUMBER})\s+(?P<title>{_TITLE})$")
_APPENDIX = re.compile(rf"^(?P<num>Appendix [A-Z]\d?)(?:\s*-?\s*(?P<title>{_TITLE}))?$")
_CAPTION = re.compile(r"^(?P<key>(?:Table|Figure)\s+[A-Z]?\d{1,2}[A-Za-z]?(?:\(\d\))?)\b(?P<rest>.*)$")
_ENDS_WITH_PAGE_REF = re.compile(r"(?:\s\d{1,3}|\.{3,}\s*\d{1,3})$")
_ADDRESS = re.compile(
    r"\b(?:Terrace|Street|Road|Avenue|Highway|Parade|Drive|Locked Bag|PO Box|GPO Box|Telephone|Phone|Fax)\b"
)
_BAD_ENDINGS = (",", ";", ":", ".", " and", " or", " the", " of", " to", " for", " in", " with")

HeadingKind = Literal["numbered", "appendix", "caption"]


@dataclass(frozen=True, slots=True)
class Heading:
    """One heading line as read, before the numbering check."""

    kind: HeadingKind
    number: str
    """'8.7.1', 'B.3.6', 'Appendix B', 'Table 6'."""
    title: str

    @property
    def text(self) -> str:
        return f"{self.number} {self.title}".strip()

    @property
    def parts(self) -> tuple[str, ...]:
        """Numbering levels: ('8', '7', '1'); ('B', '3', '6'); ('B',) for 'Appendix B'."""
        if self.kind == "appendix":
            return (self.number.split()[-1],)
        return tuple(p for p in self.number.removeprefix("Section ").rstrip(".").split(".") if p)


def _title_ok(title: str) -> bool:
    return not title.endswith(_BAD_ENDINGS) and len(title.split()) <= MAX_TITLE_WORDS and _ADDRESS.search(title) is None


def parse_heading(line: str) -> Heading | None:
    """The line as a heading or caption, or None. Contents-page entries (ending in a page number) are not headings."""
    if len(line) > 100 or _ENDS_WITH_PAGE_REF.search(line):
        return None
    caption = _CAPTION.match(line)
    if caption:
        rest = caption.group("rest")
        title = rest.strip(" -:")
        if title and not (title[:1].isupper() and rest[:1] in " -:") and not title.lower().startswith("continued"):
            return None  # a sentence that starts with a table name ('Table 3 below, together with ...')
        return Heading("caption", caption.group("key"), title)
    appendix = _APPENDIX.match(line)
    if appendix:
        title = appendix.group("title") or ""
        return Heading("appendix", appendix.group("num"), title) if not title or _title_ok(title) else None
    match = _NUMBERED.match(line)
    if not match or not _title_ok(match.group("title")):
        return None
    return Heading("numbered", match.group("num").rstrip("."), match.group("title"))


def heading_of(line: str) -> str | None:
    """The line as a heading or caption, as text ('2.5 Ecological investigation levels'), or None."""
    heading = parse_heading(line)
    return None if heading is None else (line if heading.kind == "caption" else heading.text)


def is_section_start(line: str) -> bool:
    """A numbered or appendix heading (not a caption), before any numbering check."""
    heading = parse_heading(line)
    return heading is not None and heading.kind != "caption"


def _is_continuation(line: str) -> bool:
    words = line.split()
    return (
        0 < len(words) <= MAX_CONTINUATION_WORDS
        and line[:1].islower()
        and not line.endswith((".", ":", ";"))
        and parse_heading(line) is None
    )


def read_heading(lines: Sequence[str], index: int) -> tuple[Heading | None, int]:
    """The heading starting at ``lines[index]`` and how many lines it spans (1 or 2), or (None, 1).

    A heading wrapped over up to :data:`MAX_HEADING_LINES` lines is joined ('12 Reuse of PFAS-contaminated' +
    'material including soils and' + 'water'). An appendix heading counts only near the top of a page, and a bare
    'Appendix B' takes its title from the next line.
    """
    line = lines[index]
    following = lines[index + 1] if index + 1 < len(lines) else ""
    heading, span = parse_heading(line), 1
    if heading is None or heading.kind != "caption":
        joined_text = line
        for extra in range(1, MAX_HEADING_LINES):
            if index + extra >= len(lines) or not _is_continuation(lines[index + extra]):
                break
            joined_text = f"{joined_text} {lines[index + extra]}"
            joined = parse_heading(joined_text)
            if joined is not None and joined.kind != "caption":
                heading, span = joined, extra + 1
    if heading is None or heading.kind != "appendix":
        return heading, span
    if index >= TOP_LINES_FOR_APPENDIX:
        return None, 1
    if not heading.title:
        title = following.strip(" -")
        if not (title[:1].isupper() and _title_ok(title) and parse_heading(title) is None):
            return None, 1
        return Heading("appendix", heading.number, title), 2
    return heading, span


def confirm_chapters(headings: Sequence[Heading]) -> list[bool]:
    """For each numbered heading in document order, whether its numbering can be trusted.

    A one-number heading ('7 Consideration of ...') is also how numbered lists look in extracted text. It is kept
    only when the next numbered subsection in the document belongs to it or to a later chapter: '3 Guiding principles'
    followed by '3.1' is a chapter; list items '4' to '10' followed by '3.1' are not. Subsections are always kept here
    (the tracker still checks their order).
    """
    numbered = [h for h in headings if h.kind == "numbered"]
    verdicts: list[bool] = []
    next_chapter: int | None = None
    for heading in reversed(numbered):
        first, depth = heading.parts[0], len(heading.parts)
        if not first.isdigit():
            verdicts.append(True)
        elif depth > 1:
            verdicts.append(True)
            next_chapter = int(first)
        else:
            verdicts.append(next_chapter is None or next_chapter >= int(first))
    return verdicts[::-1]


class HeadingTracker:
    """Follows the numbering through one document and keeps the path of headings in force.

    Accepted: an appendix heading; a lettered subsection of the current appendix ('B.3.6' in Appendix B); outside
    the appendices, a numbered heading that does not go back to an earlier chapter and moves at most
    :data:`MAX_CHAPTER_STEP` chapters on. Before any chapter is seen, a one-number heading must be at most
    :data:`MAX_FIRST_CHAPTER`. Numbered lines inside an appendix (checklist rows such as '14. Assessment levels')
    are not headings: appendices come last, so the main numbering does not resume.
    """

    def __init__(self, verdicts: Sequence[bool] = ()) -> None:
        """``verdicts`` come from :func:`confirm_chapters` over the same headings in the same order; without them
        every numbered heading is judged by its order alone."""
        self._verdicts = list(verdicts)
        self._seen = 0
        self._path: list[Heading] = []
        self._chapter: int | None = None
        self._appendix: str | None = None
        self._captions: dict[str, str] = {}

    @property
    def section(self) -> str | None:
        return self._path[-1].text if self._path else None

    @property
    def path(self) -> str | None:
        return " > ".join(h.text for h in self._path) if self._path else None

    def caption(self, heading: Heading) -> str:
        """The full caption for a caption line; 'Table 5 continued' becomes the caption first seen for Table 5."""
        if heading.title and not heading.title.lower().startswith("continued"):
            self._captions[heading.number] = heading.text
            return heading.text
        return self._captions.get(heading.number, heading.text)

    def accept(self, heading: Heading) -> bool:
        """Whether ``heading`` starts a new section; if so it becomes the current one."""
        if heading.kind == "caption":
            return False
        parts = heading.parts
        if heading.kind == "appendix":
            self._appendix, self._path = parts[0], [heading]
            return True
        trusted = self._verdicts[self._seen] if self._seen < len(self._verdicts) else True
        self._seen += 1
        if not trusted:
            return False
        if parts[0].isalpha():
            if parts[0] != self._appendix:
                return False
        elif self._appendix is not None or not self._chapter_fits(int(parts[0]), len(parts)):
            return False
        else:
            self._chapter = int(parts[0])
        self._path = [h for h in self._path if h.kind == "appendix" or _is_prefix(h.parts, parts)] + [heading]
        return True

    def _chapter_fits(self, chapter: int, depth: int) -> bool:
        if self._chapter is None:
            return depth > 1 or chapter <= MAX_FIRST_CHAPTER
        if depth > 1:
            return self._chapter <= chapter <= self._chapter + MAX_CHAPTER_STEP
        return self._chapter < chapter <= self._chapter + MAX_CHAPTER_STEP


def _is_prefix(parent: tuple[str, ...], child: tuple[str, ...]) -> bool:
    return len(parent) < len(child) and child[: len(parent)] == parent
