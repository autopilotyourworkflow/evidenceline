"""Concentration units for water and soil, converted exactly by moving the decimal point.

Water results are held in ug/L and soil results in mg/kg. ``ug/L``, ``µg/L`` (micro sign, U+00B5), ``μg/L``
(Greek mu, U+03BC) and ``mcg/L`` are the same unit written differently. ng/L and mg/L are converted with
``Decimal.scaleb``, so 38 ng/L becomes 0.038 ug/L with no rounding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from evidenceline.errors import EvidencelineError

Matrix = Literal["soil", "water"]

CANONICAL: dict[Matrix, str] = {"water": "ug/L", "soil": "mg/kg"}

# spelling key -> (matrix the unit belongs to, power of ten that converts it to the canonical unit)
_UNITS: dict[str, tuple[Matrix, int]] = {
    "ng/l": ("water", -3),
    "ug/l": ("water", 0),
    "mg/l": ("water", 3),
    "ug/kg": ("soil", -3),
    "mg/kg": ("soil", 0),
}

_MATRICES: dict[str, Matrix] = {"s": "soil", "soil": "soil", "w": "water", "water": "water", "groundwater": "water"}


@dataclass(frozen=True, slots=True)
class Unit:
    """A unit as written in a file, with what it means."""

    written: str
    key: str
    matrix: Matrix
    exponent: int

    @property
    def canonical(self) -> str:
        return CANONICAL[self.matrix]

    def to_canonical(self, value: Decimal) -> Decimal:
        return value.scaleb(self.exponent)


def spelling_key(raw: str) -> str:
    """``" µg / L "`` becomes ``"ug/l"``: spaces removed, lower case, micro sign and Greek mu read as u."""
    return re.sub(r"\s+", "", raw).lower().replace("µ", "u").replace("μ", "u").replace("mcg", "ug")


def parse_unit(raw: str) -> Unit:
    """Read a concentration unit. Raises EvidencelineError listing the units understood."""
    key = spelling_key(raw)
    if key not in _UNITS:
        raise EvidencelineError(
            f"Unknown unit {raw!r}. Units understood: ng/L, ug/L (also written µg/L or μg/L), mg/L, ug/kg, mg/kg."
        )
    matrix, exponent = _UNITS[key]
    return Unit(written=raw.strip(), key=key, matrix=matrix, exponent=exponent)


def parse_matrix(raw: str) -> Matrix:
    """``S``, ``Soil`` -> soil; ``W``, ``Water``, ``Groundwater`` -> water."""
    key = raw.strip().lower()
    if key not in _MATRICES:
        raise EvidencelineError(f"Unknown matrix {raw!r}. Use Soil or Water (S or W on a chain of custody).")
    return _MATRICES[key]
