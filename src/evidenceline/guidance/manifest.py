"""The corpus manifest: which public guidance documents are indexed, where they come from, and their licences.

The manifest (``evidenceline/data/corpus_manifest.json``) is the only part of the corpus kept in the package.
Downloads, extracted text and the search index live in a cache folder outside the package (see :func:`cache_dir`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

from evidenceline.errors import EvidencelineError

LicenceLane = Literal["A", "B", "C"]
DocFormat = Literal["pdf", "markdown"]

MANIFEST_NAME = "corpus_manifest.json"
CACHE_ENV = "EVIDENCELINE_CORPUS_DIR"
"""Environment variable that overrides the cache folder."""

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """One guidance document as listed in the manifest."""

    id: str
    title: str
    short_title: str
    issuer: str
    edition: str
    publication_date: str
    wa_status: str
    format: DocFormat
    official_url: str
    fetch_urls: tuple[str, ...]
    licence_lane: LicenceLane
    licence: str
    notice: str
    available: bool
    availability_note: str
    sha256: str | None
    retrieved: str | None

    @property
    def filename(self) -> str:
        """File name of the download inside the cache folder."""
        return f"{self.id}.{'pdf' if self.format == 'pdf' else 'md'}"


def _str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise EvidencelineError(f"Corpus manifest entry {raw.get('id')!r}: {key!r} must be text.")
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvidencelineError(f"Corpus manifest entry {raw.get('id')!r}: {key!r} must be text or null.")
    return value


def parse_document(raw: dict[str, Any]) -> CorpusDocument:
    """Validate one manifest entry."""
    lane = _str(raw, "licence_lane")
    if lane not in ("A", "B", "C"):
        raise EvidencelineError(f"Corpus manifest entry {raw.get('id')!r}: licence lane must be A, B or C.")
    fmt = _str(raw, "format")
    if fmt not in ("pdf", "markdown"):
        raise EvidencelineError(f"Corpus manifest entry {raw.get('id')!r}: format must be 'pdf' or 'markdown'.")
    urls = raw.get("fetch_urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in cast(list[object], urls)):
        raise EvidencelineError(f"Corpus manifest entry {raw.get('id')!r}: fetch_urls must be a list of URLs.")
    available = raw.get("available")
    if not isinstance(available, bool):
        raise EvidencelineError(f"Corpus manifest entry {raw.get('id')!r}: 'available' must be true or false.")
    return CorpusDocument(
        id=_str(raw, "id"),
        title=_str(raw, "title"),
        short_title=_str(raw, "short_title"),
        issuer=_str(raw, "issuer"),
        edition=_str(raw, "edition"),
        publication_date=_str(raw, "publication_date"),
        wa_status=_str(raw, "wa_status"),
        format=fmt,
        official_url=_str(raw, "official_url"),
        fetch_urls=tuple(cast(list[str], urls)),
        licence_lane=lane,
        licence=_str(raw, "licence"),
        notice=_str(raw, "notice"),
        available=available,
        availability_note=_str(raw, "availability_note"),
        sha256=_optional_str(raw, "sha256"),
        retrieved=_optional_str(raw, "retrieved"),
    )


def parse_manifest(text: str) -> tuple[CorpusDocument, ...]:
    """Parse the manifest JSON text into documents; ids must be unique."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(cast(dict[str, Any], data).get("documents"), list):
        raise EvidencelineError("Corpus manifest must be an object with a 'documents' list.")
    entries = cast(list[dict[str, Any]], cast(dict[str, Any], data)["documents"])
    docs = tuple(parse_document(entry) for entry in entries)
    ids = [doc.id for doc in docs]
    if len(set(ids)) != len(ids):
        raise EvidencelineError("Corpus manifest has duplicate document ids.")
    return docs


def manifest_path() -> Path:
    """Path of the packaged manifest file (the fetch script writes checksums back into it)."""
    return Path(str(resources.files("evidenceline") / "data" / MANIFEST_NAME))


@cache
def load_manifest() -> tuple[CorpusDocument, ...]:
    """The packaged manifest, parsed once."""
    return parse_manifest(manifest_path().read_text(encoding="utf-8"))


def cache_dir() -> Path:
    """Folder for downloads, extracted text and the index.

    ``$EVIDENCELINE_CORPUS_DIR`` if set, otherwise ``.cache/corpus`` at the root of the source checkout. Nothing
    here is ever written inside the package.
    """
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override)
    return _REPO_ROOT / ".cache" / "corpus"
