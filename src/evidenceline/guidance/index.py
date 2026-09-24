"""The SQLite FTS5 index: one row per passage, ranked with BM25.

Schema (one file, ``guidance.sqlite`` in the cache folder)::

    meta(key, value)                       build time, tokenizer, format version
    documents(id, sha256, pages, empty_pages, chunks)
    chunks(id, doc_id, pdf_page, printed_page, label_basis, section, section_path, headings, captions, text)
    chunks_fts(headings, captions, text)   FTS5, external content = chunks, porter + unicode61

``headings`` holds the passage's heading path ('18 PFAS sampling > 18.2 ... > 18.2.1 ...') and ``captions`` the
table and figure captions printed in it. BM25 is SQLite's built-in ``bm25()`` (k1 = 1.2, b = 0.75) with the column
weights below; smaller values are better matches. Dense embeddings are not used (the server must run on a small
host; lexical search with a synonym map is measured in :mod:`evidenceline.guidance.evaluate`).
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from evidenceline.errors import EvidencelineError
from evidenceline.guidance.chunking import Chunk
from evidenceline.guidance.labels import LabelBasis

INDEX_NAME = "guidance.sqlite"
_WORD = re.compile(r"[a-z]+")
FORMAT_VERSION = "2"
"""2: passage chunks with heading path and captions columns (1 was one chunk per page)."""
TOKENIZER = "porter unicode61 remove_diacritics 2"
HEADING_WEIGHT = 2.0
CAPTION_WEIGHT = 2.0
TEXT_WEIGHT = 1.0
"""BM25 column weights: a word in the heading path or a caption counts twice a word in the passage text."""
HEADING_COLUMNS = "{headings captions}"
"""FTS5 column filter for 'the concept is named in a heading or caption'."""
_TEXT_COLUMN = 2


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    id: str
    sha256: str
    pages: int
    empty_pages: tuple[int, ...]
    chunks: int


@dataclass(frozen=True, slots=True)
class Hit:
    """A passage returned by a search, with its BM25 score (smaller is better; used only for ordering)."""

    rowid: int
    doc_id: str
    pdf_page: int | None
    printed_page: str | None
    label_basis: LabelBasis | None
    section: str | None
    score: Decimal
    section_path: str | None = None


def build_index(path: Path, documents: Sequence[IndexedDocument], chunks: Iterable[Chunk]) -> int:
    """Write a fresh index to ``path`` (replacing any old one). Returns the number of passages indexed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".building")
    partial.unlink(missing_ok=True)
    connection = sqlite3.connect(partial)
    try:
        connection.executescript(
            f"""
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE documents(
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, pages INTEGER NOT NULL,
                empty_pages TEXT NOT NULL, chunks INTEGER NOT NULL
            );
            CREATE TABLE chunks(
                id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id), pdf_page INTEGER,
                printed_page TEXT, label_basis TEXT, section TEXT, section_path TEXT, headings TEXT NOT NULL,
                captions TEXT NOT NULL, text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                headings, captions, text, content='chunks', content_rowid='id', tokenize='{TOKENIZER}'
            );
            """
        )
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("format_version", FORMAT_VERSION),
                ("tokenizer", TOKENIZER),
                ("built_at", dt.datetime.now(dt.UTC).isoformat(timespec="seconds")),
            ],
        )
        connection.executemany(
            "INSERT INTO documents(id, sha256, pages, empty_pages, chunks) VALUES (?, ?, ?, ?, ?)",
            [(d.id, d.sha256, d.pages, ",".join(map(str, d.empty_pages)), d.chunks) for d in documents],
        )
        count = 0
        for chunk in chunks:
            captions = "; ".join(chunk.captions)
            headings = chunk.section_path or chunk.section or ""
            cursor = connection.execute(
                "INSERT INTO chunks(doc_id, pdf_page, printed_page, label_basis, section, section_path, headings, "
                "captions, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk.doc_id,
                    chunk.pdf_page,
                    chunk.printed_page,
                    chunk.label_basis,
                    chunk.section,
                    chunk.section_path,
                    headings,
                    captions,
                    chunk.text,
                ),
            )
            connection.execute(
                "INSERT INTO chunks_fts(rowid, headings, captions, text) VALUES (?, ?, ?, ?)",
                (cursor.lastrowid, headings, captions, chunk.text),
            )
            count += 1
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('optimize')")
        connection.commit()
    finally:
        connection.close()
    partial.replace(path)
    return count


class GuidanceIndex:
    """Read-only access to a built index. Safe to share between threads: the web API answers questions and MCP tool
    calls in worker threads, and one cached index serves them all, so every query and every read of its rows holds
    one lock (a query takes a few milliseconds)."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise EvidencelineError(
                f"The guidance index has not been built ({path} is missing). Run "
                "'python scripts/fetch_corpus.py' and then 'python scripts/build_index.py'."
            )
        self.path = path
        self._words: Counter[str] | None = None
        self._lock = threading.Lock()
        self._db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        version = self.meta().get("format_version")
        if version != FORMAT_VERSION:
            self.close()
            raise EvidencelineError(
                f"The guidance index at {path} is in an older format (version {version}; this code reads version "
                f"{FORMAT_VERSION}). Rebuild it with 'python scripts/build_index.py'."
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _rows(self, sql: str, parameters: Sequence[object] = ()) -> list[Any]:
        """Every row of one query, fetched while holding the lock: one connection is never used by two threads at
        once (that returned wrong rows, and raised IndexError and TypeError, when two questions came at once)."""
        with self._lock:
            return self._db.execute(sql, tuple(parameters)).fetchall()

    def meta(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self._rows("SELECT key, value FROM meta")}

    def documents(self) -> dict[str, IndexedDocument]:
        rows = self._rows("SELECT id, sha256, pages, empty_pages, chunks FROM documents")
        return {
            str(r[0]): IndexedDocument(
                id=str(r[0]),
                sha256=str(r[1]),
                pages=int(r[2]),
                empty_pages=tuple(int(p) for p in str(r[3]).split(",") if p),
                chunks=int(r[4]),
            )
            for r in rows
        }

    def chunk_count(self) -> int:
        return int(self._rows("SELECT count(*) FROM chunks")[0][0])

    def document_frequency(self, expression: str) -> int:
        """How many passages match an FTS5 expression."""
        return len(self.matching_rowids(expression))

    def matching_rowids(self, expression: str) -> frozenset[int]:
        """Every passage matching an FTS5 expression."""
        rows = self._rows("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (expression,))
        return frozenset(int(r[0]) for r in rows)

    def search(self, expression: str, limit: int, doc_ids: Sequence[str] = ()) -> list[Hit]:
        """Passages matching ``expression``, best BM25 first; only from ``doc_ids`` when any are given."""
        where_doc = f" AND c.doc_id IN ({', '.join('?' for _ in doc_ids)})" if doc_ids else ""
        rows = self._rows(
            "SELECT c.id, c.doc_id, c.pdf_page, c.printed_page, c.label_basis, c.section, c.section_path, "
            f"bm25(chunks_fts, {HEADING_WEIGHT}, {CAPTION_WEIGHT}, {TEXT_WEIGHT}) AS score "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ?{where_doc} ORDER BY score LIMIT ?",
            (expression, *doc_ids, limit),
        )
        return [
            Hit(
                rowid=int(r[0]),
                doc_id=str(r[1]),
                pdf_page=None if r[2] is None else int(r[2]),
                printed_page=None if r[3] is None else str(r[3]),
                label_basis=cast(LabelBasis | None, r[4]),
                section=None if r[5] is None else str(r[5]),
                section_path=None if r[6] is None else str(r[6]),
                score=Decimal(repr(float(r[7]))),
            )
            for r in rows
        ]

    def matches(self, rowid: int, expression: str) -> bool:
        """Whether one passage matches an FTS5 expression."""
        return bool(self._rows("SELECT 1 FROM chunks_fts WHERE rowid = ? AND chunks_fts MATCH ?", (rowid, expression)))

    def snippet(self, rowid: int, expression: str, max_tokens: int) -> str:
        """The best fragment of the passage text for ``expression``, at most ``max_tokens`` tokens (FTS5 snippet)."""
        rows = self._rows(
            "SELECT snippet(chunks_fts, ?, '', '', '...', ?) FROM chunks_fts WHERE rowid = ? AND chunks_fts MATCH ?",
            (_TEXT_COLUMN, max_tokens, rowid, expression),
        )
        return str(rows[0][0]) if rows else ""

    def text(self, rowid: int) -> str:
        rows = self._rows("SELECT text FROM chunks WHERE id = ?", (rowid,))
        return str(rows[0][0]) if rows else ""

    def word_counts(self) -> Counter[str]:
        """How often each word (lower case, letters only) appears in the passages, headings and captions. Read once
        per index and kept: it is what a misspelt word is corrected to (:mod:`evidenceline.guidance.spelling`)."""
        if self._words is None:
            words: Counter[str] = Counter()
            for row in self._rows("SELECT headings, captions, text FROM chunks"):
                for column in row:
                    words.update(_WORD.findall(str(column or "").lower()))
            self._words = words
        return self._words
