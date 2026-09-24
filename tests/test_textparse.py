from decimal import Decimal

import pytest

from evidenceline.dataset import SUM_KEY
from evidenceline.textparse import SpanSet, find_analytes, find_dates, find_numbers, find_rules, split_sentences

KNOWN = ("PFOS", "PFHxS", "PFOA", "PFBS", "6:2 FTS")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("September 2025", (2025, 9, None)),
        ("Sept 2025", (2025, 9, None)),
        ("Sep. 2025", (2025, 9, None)),
        ("sep 2025", (2025, 9, None)),
        ("16 September 2025", (2025, 9, 16)),
        ("16th Sep 2025", (2025, 9, 16)),
        ("September 16, 2025", (2025, 9, 16)),
        ("2025-09-16", (2025, 9, 16)),
        ("5 May 2026", (2026, 5, 5)),
    ],
)
def test_dates(text: str, expected: tuple[int, int, int | None]) -> None:
    dates, unclear = find_dates(f"PFOS was measured in {text}.", SpanSet())
    assert unclear == []
    assert [(d.year, d.month, d.day) for d in dates] == [expected]


def test_may_as_a_verb_is_not_a_date() -> None:
    dates, _ = find_dates("PFOS may rise.", SpanSet())
    assert dates == []


def test_impossible_and_ambiguous_dates() -> None:
    dates, unclear = find_dates("On 31 February 2025 and on 05/06/2025.", SpanSet())
    assert dates == []
    assert [s.text for s in unclear] == ["31 February 2025", "05/06/2025"]


def test_numbers_and_units() -> None:
    numbers = find_numbers("0.038 ug/L, 38 ng/L, 0.00007 mg/L, <0.001 µg/L, 1,000 ng/L, 7.3%, MB2", SpanSet())
    values = [(n.number_text, n.value_ug, n.less_than) for n in numbers]
    assert values == [
        ("0.038", Decimal("0.038"), False),
        ("38", Decimal("0.038"), False),
        ("0.00007", Decimal("0.07"), False),
        ("0.001", Decimal("0.001"), True),
        ("1,000", Decimal("1"), False),
        ("7.3", None, False),
    ]


def test_rule_names_consume_their_version_numbers() -> None:
    used = SpanSet()
    rules = find_rules("above the PFAS NEMP 3.0 value and the ADWG (2025) value; NEMP 3.1 agrees", used)
    assert [r.rule for r in rules] == ["nemp-3.0", "current", "current"]
    assert find_numbers("above the PFAS NEMP 3.0 value and the ADWG (2025) value; NEMP 3.1 agrees", used) == []


def test_analytes_sum_group_single_and_vague() -> None:
    text = "The sum of PFOS and PFHxS rose; PFOS and PFHxS fell; PFOA and 6:2 FTS were low; PFAS persisted."
    found = find_analytes(text, KNOWN, SpanSet())
    assert [(a.analytes, a.is_group_word) for a in found] == [
        ((SUM_KEY,), False),
        (("PFOS", "PFHxS"), False),
        (("PFOA", "6:2 FTS"), False),
        ((), True),
    ]


def test_full_names_map_to_abbreviations() -> None:
    found = find_analytes("Perfluorooctane sulfonate and perfluorohexane sulfonic acid", KNOWN, SpanSet())
    assert found[0].analytes == ("PFOS", "PFHxS")


def test_sentence_split_keeps_decimals_and_abbreviated_months() -> None:
    text = "PFOS was 0.038 ug/L in Sept. 2025. It fell.\n\nNew paragraph here."
    assert split_sentences(text) == ["PFOS was 0.038 ug/L in Sept. 2025.", "It fell.", "New paragraph here."]
