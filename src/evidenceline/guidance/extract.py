"""Extract page text from the downloaded guidance documents.

PDF text comes from **pypdf** (BSD-3-Clause). A page where pypdf finds almost no text is retried with
**pdfplumber** (MIT); if both find nothing the page is recorded as having no text layer, never skipped silently.
PyMuPDF is deliberately not used: it is AGPL-3.0, which would make a commercial path a licence question.

Guideline values are never read from this text. Extracted tables are known to be garbled (columns shift, footnote
markers fuse with chemical names, thousands separators are spaces), so criteria come only from the hand-verified
``guidelines.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pdfplumber
from pypdf import PdfReader

MIN_PAGE_CHARS = 20
"""Below this many non-space characters, pypdf's text is treated as empty and pdfplumber is tried."""


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    pdf_page: int
    text: str
    extractor: str
    """'pypdf', 'pdfplumber' or 'none' (no text layer)."""


@dataclass(frozen=True, slots=True)
class ExtractedPdf:
    doc_id: str
    pages: tuple[ExtractedPage, ...]
    declared_labels: tuple[str, ...]

    @property
    def empty_pages(self) -> list[int]:
        return [page.pdf_page for page in self.pages if page.extractor == "none"]

    def to_json(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "declared_labels": list(self.declared_labels),
            "pages": [{"pdf_page": p.pdf_page, "extractor": p.extractor, "text": p.text} for p in self.pages],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ExtractedPdf:
        pages = cast(list[dict[str, Any]], data["pages"])
        return cls(
            doc_id=str(data["doc_id"]),
            pages=tuple(ExtractedPage(int(p["pdf_page"]), str(p["text"]), str(p["extractor"])) for p in pages),
            declared_labels=tuple(str(label) for label in cast(list[object], data["declared_labels"])),
        )


def _visible_chars(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def extract_pdf(doc_id: str, path: Path) -> ExtractedPdf:
    """Text of every page, with the page labels the PDF declares (pypdf reports '1', '2', ... when it has none)."""
    reader = PdfReader(path)
    raw = [page.extract_text() or "" for page in reader.pages]
    pages: list[ExtractedPage] = []
    retry = [index for index, text in enumerate(raw) if _visible_chars(text) < MIN_PAGE_CHARS]
    fallback: dict[int, str] = {}
    if retry:
        with pdfplumber.open(path) as pdf:
            for index in retry:
                fallback[index] = pdf.pages[index].extract_text() or ""
    for index, text in enumerate(raw):
        if index in fallback:
            alt = fallback[index]
            if _visible_chars(alt) >= MIN_PAGE_CHARS:
                pages.append(ExtractedPage(index + 1, alt, "pdfplumber"))
            else:
                pages.append(ExtractedPage(index + 1, "", "none"))
        else:
            pages.append(ExtractedPage(index + 1, text, "pypdf"))
    return ExtractedPdf(doc_id=doc_id, pages=tuple(pages), declared_labels=tuple(reader.page_labels))


def save_extracted(extracted: ExtractedPdf, folder: Path) -> Path:
    """Write the extracted text to ``<folder>/<doc_id>.json`` (in the cache, never in the package)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{extracted.doc_id}.json"
    target.write_text(json.dumps(extracted.to_json(), ensure_ascii=False), encoding="utf-8")
    return target
