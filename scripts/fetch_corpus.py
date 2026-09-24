"""Download the public guidance documents listed in the corpus manifest into the cache folder.

Usage::

    .venv/Scripts/python scripts/fetch_corpus.py                    # fetch what is missing, check checksums
    .venv/Scripts/python scripts/fetch_corpus.py --update-manifest  # also write SHA-256 and date into the manifest
    .venv/Scripts/python scripts/fetch_corpus.py --force            # download again even if a file is cached

Each document's ``fetch_urls`` are tried in order (official host first, a pinned Wayback capture as fallback).
The files go to ``.cache/corpus/downloads/`` (or ``$EVIDENCELINE_CORPUS_DIR/downloads``); they are never copied
into the package or the repository. A document whose host cannot be reached is reported as unavailable; nothing
is invented in its place. A checksum that differs from the manifest is reported, because it means the publisher
changed the file at the same URL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from evidenceline.guidance.manifest import CorpusDocument, cache_dir, load_manifest, manifest_path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
CRAWL_DELAY_SECONDS = {"www.legislation.gov.au": 10.0}
"""Hosts whose robots.txt asks for a delay between requests."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def looks_right(doc: CorpusDocument, head: bytes) -> bool:
    """A PDF must start with the PDF signature; a Markdown page must not be an HTML error page."""
    if doc.format == "pdf":
        return head.startswith(b"%PDF")
    stripped = head.lstrip().lower()
    return not stripped.startswith((b"<!doctype", b"<html"))


class Fetcher:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self._last_request: dict[str, float] = {}

    def _wait_for(self, host: str) -> None:
        delay = CRAWL_DELAY_SECONDS.get(host, 0.0)
        last = self._last_request.get(host)
        if delay and last is not None:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def download(self, url: str, target: Path) -> None:
        self._wait_for(urlparse(url).netloc)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(request, timeout=self.timeout) as response, partial.open("wb") as out:
            while block := response.read(1 << 16):
                out.write(block)
        partial.replace(target)


def fetch_document(doc: CorpusDocument, folder: Path, fetcher: Fetcher, *, force: bool) -> dict[str, Any]:
    """Fetch one document. Returns a log record: status, url used, sha256, bytes and any errors."""
    target = folder / doc.filename
    record: dict[str, Any] = {"id": doc.id, "errors": []}
    if target.exists() and not force:
        record.update(status="cached", url=None)
    else:
        for url in doc.fetch_urls:
            try:
                fetcher.download(url, target)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                cast(list[str], record["errors"]).append(f"{url}: {exc}")
                continue
            with target.open("rb") as handle:
                head = handle.read(512)
            if not looks_right(doc, head):
                cast(list[str], record["errors"]).append(f"{url}: response is not a {doc.format} file")
                target.unlink()
                continue
            record.update(status="downloaded", url=url)
            break
        else:
            record.update(status="unavailable", url=None)
            return record
    digest = sha256_of(target)
    record.update(sha256=digest, bytes=target.stat().st_size)
    if doc.sha256 is not None and doc.sha256 != digest:
        record["checksum"] = "CHANGED: differs from the manifest, the publisher may have replaced the file"
    elif doc.sha256 is not None:
        record["checksum"] = "matches the manifest"
    else:
        record["checksum"] = "not yet recorded in the manifest"
    return record


def update_manifest(records: list[dict[str, Any]], today: str) -> None:
    """Write each fetched file's SHA-256 and retrieval date into the manifest, keeping its layout."""
    path = manifest_path()
    data = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    by_id = {r["id"]: r for r in records}
    for entry in cast(list[dict[str, Any]], data["documents"]):
        record = by_id.get(entry["id"])
        if record is None or "sha256" not in record:
            continue
        if entry.get("sha256") != record["sha256"]:
            entry["sha256"] = record["sha256"]
            entry["retrieved"] = today
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--force", action="store_true", help="download again even if the file is cached")
    parser.add_argument("--update-manifest", action="store_true", help="record SHA-256 and date in the manifest")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds per request (default 90)")
    parser.add_argument("--only", nargs="*", default=None, help="document ids to fetch (default: all)")
    args = parser.parse_args(argv)

    folder = cache_dir() / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(timeout=args.timeout)
    records: list[dict[str, Any]] = []
    for doc in load_manifest():
        if args.only and doc.id not in args.only:
            continue
        print(f"{doc.id}: fetching ...", flush=True)
        record = fetch_document(doc, folder, fetcher, force=args.force)
        records.append(record)
        detail = record.get("checksum", "; ".join(cast(list[str], record["errors"])))
        print(f"{doc.id}: {record['status']} ({detail})", flush=True)

    today = dt.datetime.now(dt.UTC).date().isoformat()
    log = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "documents": records}
    (cache_dir() / "fetch_log.json").write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    if args.update_manifest:
        update_manifest(records, today)
        print(f"Manifest updated: {manifest_path()}")
    changed = [r["id"] for r in records if str(r.get("checksum", "")).startswith("CHANGED")]
    if changed:
        print(f"Checksum changed for: {', '.join(changed)}. Re-run the golden set before trusting the index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
