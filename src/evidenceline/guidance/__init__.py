"""'Ask the guidelines': cited passage search over public WA and national contaminated-site guidance.

Pipeline: ``scripts/fetch_corpus.py`` downloads the documents in ``data/corpus_manifest.json`` into a cache folder
outside the package; ``scripts/build_index.py`` extracts page text (pypdf, pdfplumber), derives printed page
numbers, splits every page into passages of about 150 to 300 words under their section headings and writes a SQLite
FTS5 index; :func:`search_guidelines` ranks passages with BM25, a hand-written synonym map and heading and phrase
bonuses, and says "not covered" rather than returning weak matches. The MCP tool is in
:mod:`evidenceline.guidance.tools`.
"""

from evidenceline.guidance.search import search_guidelines

__all__ = ["search_guidelines"]
