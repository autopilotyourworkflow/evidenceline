"""Read every number in a piece of text, with the kind of quantity its unit makes it. No model is involved.

A concentration is recognised however its unit is written: ``ug/L``, ``µg/L``, ``μg/L``, ``mcg/L``, ``ng/L``,
``mg/L``, ``ug L-1``, ``micrograms per litre``, ``ng/litre``, ``ppt``, ``parts per billion`` and so on. That matters
because a concentration whose unit is not recognised would be read as a bare number and could then be traced to any
number in a passage, including badly extracted table text.

Kinds:

- ``water``: a concentration in water, converted exactly to ug/L (parts per trillion, billion and million are read
  as ng/L, ug/L and mg/L);
- ``soil``: a concentration per kilogram or per gram;
- ``percent``;
- ``label``: a dotted section number such as 9.1.4;
- ``unknown``: a number with a mass unit and no volume (``0.5 ug``), which is never traced;
- ``plain``: anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from evidenceline.units import parse_decimal, to_ug_per_l

Kind = Literal["plain", "label", "water", "soil", "percent", "unknown"]

_BRACKET = re.compile(r"\[(?=G?\d)([^\]]*)\]")
"""Anything in square brackets that starts like a citation: [1], [G2], [1, 3], [1-3]."""
_PLACEHOLDER = re.compile(r"\[[A-Z]+-\d+\]")
"""Redaction placeholders such as [CLIENT-1]: their numbers are not values."""

_MICRO = r"(?:[u\u00b5\u03bc]g|mcg|micrograms?|microgrammes?)"
_NANO = r"(?:ng|nanograms?|nanogrammes?)"
_MILLI = r"(?:mg|milligrams?|milligrammes?)"
_MASS = rf"(?:{_MICRO}|{_NANO}|{_MILLI})"
_PER = r"\s*(?:/|\bper\b|\bin\s+(?:one|a|each)\b)\s*"
_LITRE = r"(?:litres?|liters?|l)(?![a-z])"
_INVERSE_LITRE = r"\s*l\s*(?:-\s*1|\u207b\u00b9|\u22121)(?!\d)"
"""'ug L-1', 'ug L^-1' or 'ug L-1'."""
_PER_KG = r"(?:kilograms?|kilogrammes?|kg|grams?|grammes?|g)(?![a-z])"
_PARTS = r"(?:pp[tbm](?![a-z])|parts?\s+per\s+(?:trillion|billion|million))"

_UNIT = (
    rf"(?P<water>{_MASS}(?:{_PER}{_LITRE}|{_INVERSE_LITRE}))"
    rf"|(?P<soil>{_MASS}{_PER}{_PER_KG})"
    rf"|(?P<parts>{_PARTS})"
    r"|(?P<percent>%|per\s*cent(?![a-z])|percent(?![a-z]))"
    rf"|(?P<mass>{_MASS}(?![a-z]))"
)
_NUMBER = re.compile(
    r"(?<!\d)(?<!\d\.)(?:(?P<label>\d+(?:\.\d+){2,})|(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))(?![\d])"
    rf"(?:\s*(?:{_UNIT}))?",
    re.IGNORECASE,
)

_PARTS_AS = {"t": "ng/l", "b": "ug/l", "m": "mg/l"}
"""Parts per trillion, billion and million in water, as mass per litre."""


@dataclass(frozen=True, slots=True)
class Quantity:
    """A number as read from text: where it is, the value as written, and in a canonical unit for its kind."""

    text: str
    kind: Kind
    raw: Decimal
    canonical: Decimal
    start: int = 0
    """Where the number starts in the text it was read from (citation markers and placeholders keep their length)."""


def _mass_key(unit: str) -> str:
    lowered = unit.lower()
    if re.match(_NANO, lowered):
        return "ng/l"
    if re.match(_MILLI, lowered):
        return "mg/l"
    return "ug/l"


def _parts_key(unit: str) -> str:
    lowered = unit.lower()
    letter = lowered[2] if lowered.startswith("pp") else re.sub(r".*per\s+", "", lowered)[0]
    return _PARTS_AS[letter]


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def read_numbers(text: str) -> list[Quantity]:
    """Every number in ``text`` with its unit kind. Citation markers and redaction placeholders are skipped."""
    cleaned = _PLACEHOLDER.sub(_blank, _BRACKET.sub(_blank, text))
    found: list[Quantity] = []
    for match in _NUMBER.finditer(cleaned):
        start = match.start()
        written = match.group(0).strip()
        if match.group("label"):
            found.append(Quantity(match.group("label"), "label", Decimal(0), Decimal(0), start))
            continue
        raw = parse_decimal(match.group("num"))
        if match.group("water"):
            key = _mass_key(match.group("water"))
            found.append(Quantity(written, "water", raw, to_ug_per_l(raw, key), start))
        elif match.group("parts"):
            key = _parts_key(match.group("parts"))
            found.append(Quantity(written, "water", raw, to_ug_per_l(raw, key), start))
        elif match.group("soil"):
            found.append(Quantity(written, "soil", raw, raw, start))
        elif match.group("percent"):
            found.append(Quantity(written, "percent", raw, raw, start))
        elif match.group("mass"):
            found.append(Quantity(written, "unknown", raw, raw, start))
        else:
            found.append(Quantity(written, "plain", raw, raw, start))
    return found
