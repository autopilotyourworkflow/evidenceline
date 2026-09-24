from decimal import Decimal

import pytest

from evidenceline.units import fmt, fmt_ug, normalise_unit, parse_decimal, percent_change, to_ug_per_l


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("8", "ng/L", "0.008"),
        ("0.008", "ug/L", "0.008"),
        ("0.00007", "mg/L", "0.07"),
        ("38", "NG/L", "0.038"),
        ("0.03", "µg/L", "0.03"),  # micro sign U+00B5
        ("0.03", "μg/L", "0.03"),  # Greek mu U+03BC
        ("1,000", "ng/L", "1"),
    ],
)
def test_unit_conversion_is_exact(value: str, unit: str, expected: str) -> None:
    assert to_ug_per_l(parse_decimal(value), unit) == Decimal(expected)


def test_eight_ng_per_litre_equals_the_pfos_limit_exactly() -> None:
    assert to_ug_per_l(Decimal(8), "ng/L") == Decimal("0.008")
    assert to_ug_per_l(Decimal(8), "ng/L") == to_ug_per_l(Decimal("0.000008"), "mg/L")


def test_decimal_arithmetic_has_no_float_error() -> None:
    assert Decimal("0.038") + Decimal("0.019") == Decimal("0.057")
    assert fmt(Decimal("0.038") + Decimal("0.019")) == "0.057"


@pytest.mark.parametrize("unit", ["ppb", "g/L", "ug/kg", ""])
def test_unknown_units_are_rejected(unit: str) -> None:
    assert normalise_unit(unit) is None
    with pytest.raises(ValueError, match="Unknown concentration unit"):
        to_ug_per_l(Decimal(1), unit)


@pytest.mark.parametrize("text", ["abc", "1e-3", "-0.5", "", "0.0.1"])
def test_parse_decimal_rejects_non_plain_numbers(text: str) -> None:
    with pytest.raises(ValueError, match="Not a plain decimal"):
        parse_decimal(text)


def test_formatting() -> None:
    assert fmt(Decimal("0.070")) == "0.07"
    assert fmt(Decimal("1E+2")) == "100"
    assert fmt(Decimal("0.000")) == "0"
    assert fmt_ug(Decimal("0.008")) == "0.008 ug/L"


def test_percent_change() -> None:
    assert percent_change(Decimal("0.041"), Decimal("0.038")) == Decimal("-7.3")
    assert percent_change(Decimal(0), Decimal(1)) is None
