"""Exact arithmetic for relative percent difference (RPD) and ratios. Decimal only, never floats."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from evidenceline.units import fmt

_PRECISION = 50


def rpd_exceeds(a: Decimal, b: Decimal, threshold: Decimal) -> bool:
    """True when RPD(a, b) is greater than ``threshold`` percent, decided exactly by cross-multiplying:
    ``|a - b| / ((a + b) / 2) x 100 > t`` is ``|a - b| x 200 > t x (a + b)``."""
    return abs(a - b) * 200 > threshold * (a + b)


_MAX_PLACES = 6


def rpd_shown(a: Decimal, b: Decimal, places: int = 1) -> Decimal:
    """RPD in percent, rounded half up to ``places`` decimal places for display. ``a + b`` must be above zero."""
    with localcontext() as context:
        context.prec = _PRECISION
        return (abs(a - b) * 200 / (a + b)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def rpd_places(a: Decimal, b: Decimal, threshold: Decimal) -> int:
    """Decimal places needed so an RPD above ``threshold`` is also shown above it.

    30.0085% rounds to 30.0% at one place, which would read as "30.0% is above 30%"; two places show 30.01%.
    Returns 1 when the RPD is not above the threshold or one place already shows the difference.
    """
    places = 1
    if not rpd_exceeds(a, b, threshold):
        return places
    while places < _MAX_PLACES and rpd_shown(a, b, places) <= threshold:
        places += 1
    return places


def rpd_working(a: Decimal, b: Decimal, places: int = 1) -> str:
    """``|180 - 95| / ((180 + 95) / 2) x 100 = 85 / 137.5 x 100 = 61.8%``."""
    difference, mean = abs(a - b), (a + b) / 2
    return (
        f"|{fmt(a)} - {fmt(b)}| / (({fmt(a)} + {fmt(b)}) / 2) x 100 = {fmt(difference)} / {fmt(mean)} x 100 = "
        f"{rpd_shown(a, b, places)}%"
    )


def multiple(value: Decimal, of: Decimal) -> Decimal:
    """``value / of`` to two decimal places, half up (for example 0.0034 / 0.003 = 1.13)."""
    with localcontext() as context:
        context.prec = _PRECISION
        return (value / of).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
