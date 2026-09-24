"""Read one CSV file into records that keep their spreadsheet row numbers and raw text.

Lines starting with ``#`` before the header are a preamble (a field sheet header, a "synthetic data" label).
``Key: value`` lines in the preamble are kept separately and never copied into tool output, because a field sheet
header can name the client and the site address. Row numbers are the file's own line numbers, as a spreadsheet
shows them.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from evidenceline.errors import EvidencelineError


@dataclass(frozen=True, slots=True)
class Record:
    """One data row: its line number and its cells by column name (cells are stripped)."""

    row: int
    cells: Mapping[str, str]

    def get(self, column: str) -> str:
        return self.cells.get(column, "")


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    header_row: int
    columns: tuple[str, ...]
    records: tuple[Record, ...]
    lines: tuple[str, ...]
    preamble: Mapping[str, str]

    def line(self, row: int) -> str:
        """The raw text of line ``row`` (1-based)."""
        return self.lines[row - 1]


def read_table(name: str, text: str, required: Sequence[str]) -> Table:
    """Parse ``text`` as CSV with an optional ``#`` preamble. Raises EvidencelineError on a malformed file."""
    lines = tuple(text.lstrip("﻿").splitlines())
    preamble: dict[str, str] = {}
    start = 0
    while start < len(lines) and (not lines[start].strip() or lines[start].lstrip().startswith("#")):
        body = lines[start].strip().lstrip("#").strip()
        key, sep, value = body.partition(":")
        if sep:
            preamble[key.strip().lower()] = value.strip()
        start += 1
    if start == len(lines):
        raise EvidencelineError(f"{name} is empty: it has no header row.")
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    columns = tuple(cell.strip() for cell in next(reader))
    missing = [column for column in required if column not in columns]
    if missing:
        raise EvidencelineError(
            f"{name} is missing columns: {', '.join(missing)}. The header (row {start + 1}) has: {', '.join(columns)}."
        )
    repeated = sorted({column for column in columns if columns.count(column) > 1})
    if repeated:
        raise EvidencelineError(f"{name} has repeated columns: {', '.join(repeated)}.")
    records: list[Record] = []
    previous_end = start + 1
    for cells in reader:
        first, previous_end = previous_end + 1, start + reader.line_num
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) != len(columns):
            raise EvidencelineError(f"{name} row {first} has {len(cells)} cells; the header has {len(columns)}.")
        records.append(Record(row=first, cells=dict(zip(columns, (cell.strip() for cell in cells), strict=True))))
    if not records:
        raise EvidencelineError(f"{name} has a header but no data rows.")
    return Table(
        name=name,
        header_row=start + 1,
        columns=columns,
        records=tuple(records),
        lines=lines,
        preamble=preamble,
    )
