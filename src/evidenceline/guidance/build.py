"""Build the search index from the downloaded documents: extract, derive page labels, chunk, index.

Used by ``scripts/build_index.py``. Everything is written to the cache folder, never to the package.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from evidenceline.guidance.chunking import Chunk, chunk_markdown, chunk_pdf_pages
from evidenceline.guidance.extract import ExtractedPdf, extract_pdf, save_extracted
from evidenceline.guidance.index import INDEX_NAME, IndexedDocument, build_index
from evidenceline.guidance.labels import derive_page_labels
from evidenceline.guidance.manifest import CorpusDocument


@dataclass
class BuildReport:
    index_path: Path
    chunks: int = 0
    documents: list[IndexedDocument] = field(default_factory=list[IndexedDocument])
    skipped: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_or_extract(doc: CorpusDocument, pdf: Path, text_dir: Path, digest: str) -> ExtractedPdf:
    cached = text_dir / f"{doc.id}.json"
    stamp = text_dir / f"{doc.id}.sha256"
    if cached.exists() and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == digest:
        return ExtractedPdf.from_json(json.loads(cached.read_text(encoding="utf-8")))
    extracted = extract_pdf(doc.id, pdf)
    save_extracted(extracted, text_dir)
    stamp.write_text(digest + "\n", encoding="utf-8")
    return extracted


def build_corpus_index(cache: Path, documents: Sequence[CorpusDocument]) -> BuildReport:
    """Index every document whose download is in ``cache/downloads``; report the ones that are missing."""
    downloads, text_dir = cache / "downloads", cache / "text"
    report = BuildReport(index_path=cache / INDEX_NAME)
    all_chunks: list[Chunk] = []
    for doc in documents:
        source = downloads / doc.filename
        if not source.exists():
            reason = doc.availability_note if not doc.available else "not downloaded yet (run fetch_corpus.py)"
            report.skipped.append(f"{doc.id}: {reason}")
            continue
        digest = _sha256(source)
        if doc.sha256 is not None and doc.sha256 != digest:
            report.warnings.append(
                f"{doc.id}: the downloaded file's SHA-256 differs from the manifest; the publisher may have "
                "changed it. Check it and re-run the golden set."
            )
        if doc.format == "pdf":
            extracted = _load_or_extract(doc, source, text_dir, digest)
            texts = [page.text for page in extracted.pages]
            labels = derive_page_labels(texts, extracted.declared_labels)
            chunks = chunk_pdf_pages(doc.id, texts, labels)
            pages, empty = len(texts), tuple(extracted.empty_pages)
        else:
            chunks = chunk_markdown(doc.id, source.read_text(encoding="utf-8"))
            pages, empty = 0, ()
        all_chunks.extend(chunks)
        report.documents.append(IndexedDocument(doc.id, digest, pages, empty, len(chunks)))
    report.chunks = build_index(report.index_path, report.documents, all_chunks)
    return report
