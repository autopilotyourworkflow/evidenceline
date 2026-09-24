"""One guidance index shared by many threads: the web API runs each question, and each MCP tool call, in a worker
thread, and every one of them reads the same cached index. Two questions at once used to read each other's rows
through the one SQLite connection (wrong passages, IndexError, TypeError, a false "not covered").

The first test is deterministic: it watches the connection itself and fails if two queries are ever inside it at
once. The others run real searches from 8 threads against a sequential baseline, on the real index when it is built.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from evidenceline.answer import answer
from evidenceline.guidance.index import INDEX_NAME, GuidanceIndex, IndexedDocument, build_index
from evidenceline.guidance.search import default_index_path, search_guidelines

from .test_guidance_search import CHUNKS

THREADS = 8
real_index = pytest.mark.skipif(not default_index_path().exists(), reason="guidance index not built")
QUESTIONS = (
    "How should groundwater samples be collected?",
    "What should a detailed site investigation report include?",
    "What is a conceptual site model?",
    "What are health investigation levels for soil?",
    "What is a tier 1 screening assessment?",
    "What quality checks should a lab report include?",
    "What is the PFOA drinking water limit under PFAS NEMP 3.0?",
    "When do I have to report a suspected contaminated site to DWER?",
    "How many field duplicate samples should be collected?",
    "What are ecological investigation levels?",
    "What is the drinking-water limit for PFOS?",
    "What does a contaminated sites auditor do?",
)


class _Watched:
    """Stands in for the index's SQLite connection and records how many queries are inside it at once. A query is
    inside from ``execute`` until its rows are fetched; the short sleep makes any overlap easy to catch."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._guard = threading.Lock()
        self.inside = 0
        self.most = 0
        self.queries = 0

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> _Rows:
        with self._guard:
            self.inside += 1
            self.queries += 1
            self.most = max(self.most, self.inside)
        time.sleep(0.001)
        return _Rows(self, self._db.execute(sql, parameters))

    def left(self) -> None:
        with self._guard:
            self.inside -= 1

    def close(self) -> None:
        self._db.close()


class _Rows:
    def __init__(self, owner: _Watched, cursor: Any) -> None:
        self._owner = owner
        self._cursor = cursor

    def fetchall(self) -> list[Any]:
        try:
            return list(self._cursor.fetchall())
        finally:
            self._owner.left()


def _in_threads(work: Callable[[str], Any], questions: Sequence[str], rounds: int) -> list[tuple[str, Any]]:
    batch = [q for _ in range(rounds) for q in questions]
    with ThreadPoolExecutor(THREADS) as pool:
        return list(zip(batch, pool.map(work, batch), strict=True))


def test_no_two_queries_are_ever_inside_the_connection_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / INDEX_NAME
    documents = [IndexedDocument(d, "0" * 64, 20, (), 1) for d in ("syn-a", "syn-b", "syn-c")]
    build_index(path, documents, CHUNKS)
    index = GuidanceIndex(path)
    watched = _Watched(index._db)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(index, "_db", watched)

    def work(expression: str) -> tuple[int, int, str]:
        rowids = index.matching_rowids(expression)
        hits = index.search(expression, 5)
        return len(rowids), index.chunk_count(), index.snippet(hits[0].rowid, expression, 10) if hits else ""

    expressions = ('"groundwater"', '"detailed site investigation"', '"perfluorooctane sulfonate"', '"potable"')
    sequential = {e: work(e) for e in expressions}
    results = _in_threads(work, expressions, rounds=10)
    index.close()
    assert watched.queries > 100
    assert watched.most == 1, f"{watched.most} queries were inside the connection at once"
    assert all(result == sequential[e] for e, result in results)


def _search_key(question: str) -> tuple[Any, ...]:
    found = search_guidelines(question, 8)
    return found.status, found.explanation, tuple((p.document_id, p.pdf_page, p.excerpt) for p in found.passages)


def _answer_key(question: str) -> tuple[Any, ...]:
    result = answer(question, None)
    return result.status, result.explanation, tuple((c.document_id, c.pdf_page, c.excerpt) for c in result.citations)


@real_index
@pytest.mark.parametrize("work", [_search_key, _answer_key], ids=["search_guidelines", "answer"])
def test_eight_threads_get_exactly_the_sequential_results(work: Callable[[str], tuple[Any, ...]]) -> None:
    sequential = {q: work(q) for q in QUESTIONS}
    errors: list[str] = []

    def guarded(question: str) -> tuple[Any, ...] | None:
        try:
            return work(question)
        except Exception as exc:  # every exception is a failure here, reported by name
            errors.append(f"{type(exc).__name__}: {question}")
            return None

    results = _in_threads(guarded, QUESTIONS, rounds=4)
    assert errors == []
    different = [q for q, result in results if result != sequential[q]]
    assert different == [], f"{len(different)} of {len(results)} threaded results differ from the sequential ones"
