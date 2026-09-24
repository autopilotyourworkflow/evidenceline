"""Build the 'Ask the guidelines' search index from the documents fetched by ``fetch_corpus.py``.

Usage::

    .venv/Scripts/python scripts/build_index.py

Reads ``.cache/corpus/downloads/`` (or ``$EVIDENCELINE_CORPUS_DIR/downloads``), writes extracted text to
``.cache/corpus/text/`` and the SQLite FTS5 index to ``.cache/corpus/guidance.sqlite``. Nothing is written into the
package or the repository. Each page is split into passages of about 150 to 300 words that never cross a page (see
``evidenceline.guidance.passages``).
"""

from __future__ import annotations

import sys

from evidenceline.guidance.build import build_corpus_index
from evidenceline.guidance.manifest import cache_dir, load_manifest


def main() -> int:
    try:
        report = build_corpus_index(cache_dir(), load_manifest())
    except PermissionError as exc:
        print(
            f"The index could not be replaced ({exc}). Another program has it open, usually a running Evidenceline "
            "server or a test run. Stop it and run this script again.",
            file=sys.stderr,
        )
        return 1
    for doc in report.documents:
        empty = f", {len(doc.empty_pages)} pages without text" if doc.empty_pages else ""
        pages = f"{doc.pages} pages" if doc.pages else "web page"
        print(f"indexed {doc.id}: {pages}, {doc.chunks} passages{empty}")
    for line in report.skipped:
        print(f"not indexed {line}")
    for line in report.warnings:
        print(f"WARNING {line}")
    print(f"{report.chunks} passages written to {report.index_path}")
    return 0 if report.documents else 1


if __name__ == "__main__":
    sys.exit(main())
