"""Exact concentration handling.

Every concentration is held as a :class:`~decimal.Decimal` in micrograms per litre (ug/L). Floats are never
used, so 0.038 stays 0.038 and 8 ng/L equals 0.008 ug/L exactly.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CANONICAL_UNIT = "ug/L"

# Power of ten that converts one unit into ug/L.
_EXPONENT_TO_UG_PER_L: dict[str, int] = {"ng/l": -3, "ug/l": 0, "mg/l": 3}

UNIT_PATTERN = r"(?:[uµμ]g|mcg|ng|mg)\s*/\s*l"
"""Regex fragment (case-insensitive) for the concentration units the checker understands."""


def normalise_unit(raw: str) -> str | None:
    """Return a lower-case key such as ``"ug/l"`` for a unit string, or None if it is not a known unit."""
    key = re.sub(r"\s+", "", raw).lower().replace("µ", "u").replace("μ", "u").replace("mcg", "ug")
    return key if key in _EXPONENT_TO_UG_PER_L else None


def to_ug_per_l(value: Decimal, unit: str) -> Decimal:
    """Convert ``value`` in ``unit`` to ug/L exactly (a shift of the decimal point, no rounding)."""
    key = normalise_unit(unit)
    if key is None:
        raise ValueError(f"Unknown concentration unit {unit!r}. Use ug/L, ng/L or mg/L.")
    return value.scaleb(_EXPONENT_TO_UG_PER_L[key])


def parse_decimal(text: str) -> Decimal:
    """Parse a plain decimal such as ``"0.038"`` or ``"1,000"``. Raises ValueError on anything else."""
    cleaned = text.replace(",", "").strip()
    if not re.fullmatch(r"\d*\.?\d+", cleaned):
        raise ValueError(f"Not a plain decimal number: {text!r}")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:  # pragma: no cover - the regex above already guards this
        raise ValueError(f"Not a plain decimal number: {text!r}") from exc


def fmt(value: Decimal) -> str:
    """Format a decimal without trailing zeros or exponent: ``Decimal("0.070")`` becomes ``"0.07"``."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def fmt_ug(value: Decimal) -> str:
    """Format a concentration with its unit, for example ``"0.038 ug/L"``."""
    return f"{fmt(value)} {CANONICAL_UNIT}"


def percent_change(before: Decimal, after: Decimal) -> Decimal | None:
    """Percentage change from ``before`` to ``after``, rounded to one decimal place. None if ``before`` is 0."""
    if before == 0:
        return None
    return ((after - before) / before * 100).quantize(Decimal("0.1"))
