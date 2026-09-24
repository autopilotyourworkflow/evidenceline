"""Turn extracted pages into searchable passages: clean the text, drop running headers, find section headings.

A PDF page is split into passages of about 150 to 300 words (:mod:`evidenceline.guidance.passages`) that never
cross a page, so every passage cites exactly one PDF page and its printed page number, with the nearest section
heading and the path of headings above it. A Markdown web page has no pages; it is chunked by section heading and
long sections are split the same way. Text is only tidied (spacing, bullet and dash characters, words split across
lines); the words themselves are never changed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from evidenceline.guidance.headings import HeadingTracker, confirm_chapters, heading_of, is_section_start
from evidenceline.guidance.labels import LabelBasis, PageLabel
from evidenceline.guidance.passages import MAX_WORDS, scan_headings, split_page, window_spans
from evidenceline.guidance.textclean import clean_line, join_lines

__all__ = [
    "Chunk",
    "chunk_markdown",
    "chunk_pdf_pages",
    "clean_line",
    "find_boilerplate",
    "heading_of",
    "is_contents_page",
    "join_lines",
]

BOILERPLATE_SHARE = 0.3
"""A top or bottom line (digits ignored) repeated on at least this share of pages is a running header or footer."""
MIN_PAGES_FOR_BOILERPLATE = 5
SKIPPED_MARKDOWN_SECTIONS = frozenset({"references"})
"""Reference lists are not guidance text and would match almost any chemical name."""
CONTENTS_MIN_LINES = 8
CONTENTS_SHARE = 0.5
"""A page where at least this share of lines (and at least CONTENTS_MIN_LINES) end in a page number is a table
of contents or list of tables. It is not indexed: it matches every topic and says nothing about any."""
BACK_MATTER_TOP_LINES = 3
"""A back-matter heading within this many lines of the top of a page makes the whole page back matter."""
CONTENTS_RISING_SHARE = 0.85
"""On a contents page the trailing page numbers go up (or stay level) from line to line."""
MIN_CHUNK_WORDS = 5

_TRAILING_NUMBER = re.compile(r"(?:\s|\.{3,}\s*)(\d{1,3})$")
_WORD = re.compile(r"[A-Za-z]{3,}")
_BACK_MATTER = re.compile(
    r"^(?:\d{1,2}\.?\s+|Appendix [A-Z]\s+)?"
    r"(?:References|Bibliography|Shortened forms|Abbreviations(?: and glossary)?)$",
    re.IGNORECASE,
)
_NUMBER_OR_LETTER = re.compile(r"\b(?:\d+|[A-Z])\b")
_PART_WORD = re.compile(r"\b(?:Section|Appendix|Chapter|Part)\b")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_MD_NOISE = re.compile(r"\*{1,3}|&#x20;|\\$")


def _boilerplate_key(line: str) -> str:
    """Numbers, single capital letters and the words 'Section' and 'Appendix' are masked, so the running headers
    'PFAS NEMP 3.0 Section 18 161' and 'PFAS NEMP 3.0 Appendix B 218' count as one repeated line."""
    masked = _NUMBER_OR_LETTER.sub("#", re.sub(r"\d+", "#", clean_line(line)))
    return _PART_WORD.sub("#", masked).lower()


def find_boilerplate(pages: Sequence[str], edge: int = 3) -> set[str]:
    """Keys of running header and footer lines: edge lines repeated on many pages (digits ignored)."""
    if len(pages) < MIN_PAGES_FOR_BOILERPLATE:
        return set()
    counts: Counter[str] = Counter()
    for text in pages:
        lines = [line for line in text.splitlines() if line.strip()]
        edges = {_boilerplate_key(line) for line in lines[:edge] + lines[-edge:]}
        counts.update(key for key in edges if key.strip("#. "))
    threshold = max(2, int(len(pages) * BOILERPLATE_SHARE))
    return {key for key, count in counts.items() if count >= threshold}


def is_contents_page(lines: Sequence[str]) -> bool:
    """True for a contents page, list of tables or list of figures.

    Most lines are words followed by a page number, and those numbers rarely go down. Rows of a data table also
    end in numbers, but they are mostly numbers and their last values do not steadily rise.
    """
    trailing = [
        int(m.group(1)) for line in lines if (m := _TRAILING_NUMBER.search(line)) and _WORD.search(line[: m.start()])
    ]
    if len(trailing) < CONTENTS_MIN_LINES or len(trailing) < CONTENTS_SHARE * len(lines):
        return False
    rising = sum(1 for a, b in pairwise(trailing) if b >= a)
    return rising >= CONTENTS_RISING_SHARE * (len(trailing) - 1)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One searchable passage with its location."""

    doc_id: str
    ordinal: int
    pdf_page: int | None
    printed_page: str | None
    label_basis: LabelBasis | None
    section: str | None
    """The nearest section heading."""
    text: str
    section_path: str | None = None
    """The headings above the passage, outermost first ('18 PFAS sampling > 18.2 ... > 18.2.1 ...')."""
    captions: tuple[str, ...] = ()
    """Table and figure captions printed in the passage ('Table 6 Ecological guideline values for soil')."""

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _page_lines(raw: str, boilerplate: set[str]) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or _boilerplate_key(line) in boilerplate:
            continue
        cleaned = clean_line(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def chunk_pdf_pages(
    doc_id: str, pages: Sequence[str], labels: Sequence[PageLabel], carry_section: str | None = None
) -> list[Chunk]:
    """Passages of every page with text. Running headers and footers are dropped; each passage carries the nearest
    section heading on its page, or the last one seen before it (``carry_section`` before the first heading).

    Not indexed: pages with no text layer, contents pages, and back matter (reference lists, bibliographies and
    lists of shortened forms or abbreviations), which match almost any question through titles and acronyms. Back matter
    starts at a heading such as '7 Bibliography' and ends at the next section or appendix heading; a page where
    the back-matter heading appears below the top is still indexed for the text above it.
    """
    kept_pages = _indexable_pages(pages)
    headings = [h for lines in kept_pages.values() for _, h, _ in scan_headings(lines) if h is not None]
    tracker = HeadingTracker(confirm_chapters(headings))
    chunks: list[Chunk] = []
    for index, kept in kept_pages.items():
        label = labels[index]
        for passage in split_page(kept, tracker):
            if len(passage.text.split()) < MIN_CHUNK_WORDS:
                continue
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    ordinal=len(chunks),
                    pdf_page=index + 1,
                    printed_page=label.label,
                    label_basis=label.basis,
                    section=passage.section or carry_section,
                    text=passage.text,
                    section_path=passage.section_path or carry_section,
                    captions=passage.captions,
                )
            )
    return chunks


def _indexable_pages(pages: Sequence[str]) -> dict[int, list[str]]:
    """Cleaned lines of each page worth indexing, by page index (see :func:`chunk_pdf_pages`)."""
    boilerplate = find_boilerplate(pages)
    in_back_matter = False
    kept_pages: dict[int, list[str]] = {}
    for index, raw in enumerate(pages):
        kept = _page_lines(raw, boilerplate)
        top = kept[:BACK_MATTER_TOP_LINES]
        back_matter_at = next((i for i, line in enumerate(kept) if _BACK_MATTER.match(line)), None)
        if in_back_matter and any(is_section_start(line) for line in top) and back_matter_at is None:
            in_back_matter = False
        if back_matter_at is not None:
            in_back_matter = True
            if back_matter_at < BACK_MATTER_TOP_LINES:
                continue
            kept = kept[:back_matter_at]
        elif in_back_matter:
            continue
        if len(join_lines(kept).split()) >= MIN_CHUNK_WORDS and not is_contents_page(kept):
            kept_pages[index] = kept
    return kept_pages


def _clean_markdown(line: str) -> str:
    line = _MD_NOISE.sub("", line.replace("&#x20;", " "))
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # links: keep the text
    line = line.replace("\\[", "[").replace("\\]", "]").replace("\\", "")
    return clean_line(line)


def _markdown_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """(heading path, raw lines) per heading. The path leaves out the page title: 'Health considerations > PFOS'."""
    path: list[tuple[int, str]] = []
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for raw in text.splitlines():
        if raw.startswith(">"):
            continue  # the site's own "see llms.txt" banner
        match = _MD_HEADING.match(raw)
        if match:
            level, title = len(match.group(1)), _clean_markdown(match.group(2))
            path = [(lvl, t) for lvl, t in path if lvl < level] + [(level, title)]
            shown = " > ".join(t for lvl, t in path if lvl > 1) or title
            sections.append((shown, []))
            continue
        sections[-1][1].append(raw.rstrip().rstrip("\\"))
    return sections


def _paragraph_blocks(paragraphs: Sequence[str]) -> list[str]:
    """Paragraphs packed into blocks of at most MAX_WORDS words; a longer paragraph is cut into windows."""
    blocks: list[str] = []
    buffer: list[str] = []
    for paragraph in [*paragraphs, ""]:
        words = sum(len(p.split()) for p in buffer)
        if buffer and (not paragraph or words + len(paragraph.split()) > MAX_WORDS):
            blocks.append(" ".join(buffer))
            buffer = []
        if paragraph:
            buffer.append(paragraph)
    passages: list[str] = []
    for block in blocks:
        words = block.split()
        passages.extend(" ".join(words[start:end]) for start, end in window_spans(len(words)))
    return passages


def chunk_markdown(doc_id: str, text: str) -> list[Chunk]:
    """Chunk a Markdown page by heading; the section is the heading path, for example 'Guideline' or
    'Health considerations > PFOS'. Long sections are split at blank lines, and long paragraphs into windows.
    """
    chunks: list[Chunk] = []
    for section, lines in _markdown_sections(text):
        if section is not None and section.split(" > ")[-1].lower() in SKIPPED_MARKDOWN_SECTIONS:
            continue
        paragraphs = [p for p in (_clean_markdown(p) for p in "\n".join(lines).split("\n\n")) if p]
        for passage in _paragraph_blocks(paragraphs):
            if len(passage.split()) >= MIN_CHUNK_WORDS:
                chunks.append(Chunk(doc_id, len(chunks), None, None, None, section, passage, section_path=section))
    return chunks
