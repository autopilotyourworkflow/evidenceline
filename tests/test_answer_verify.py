"""The answer checks, the question routes and the verified guideline values, each on its own. No model, no index."""

from __future__ import annotations

import pytest

from evidenceline.answer.context import PassageText, tidy_dashes
from evidenceline.answer.models import GuidelineValue
from evidenceline.answer.routing import asks_for_verdict, drinking_water_analytes
from evidenceline.answer.values import describe, guideline_values
from evidenceline.answer.verify import citations, read_numbers, sentences, verify

PASSAGES = [
    PassageText(1, "[1] Guideline: Assessment and management of contaminated sites. p. 29 (PDF p. 34).",
                "A detailed site investigation report includes a sampling plan, 95 per cent confidence limits and "
                "results for 12 wells, as set out in section 9.2.1."),
    PassageText(2, "[2] Identification, reporting and classification. p. 25 (PDF p. 30).",
                "A known or suspected contaminated site must be reported within 21 days. A table lists 0.5 ug/L."),
]  # fmt: skip


def _values(*analytes: str) -> list[GuidelineValue]:
    return guideline_values(analytes)


def _checks(answer: str, values: list[GuidelineValue] | None = None) -> dict[str, bool]:
    given = values or []
    record = verify(answer, PASSAGES, given, [describe(v) for v in given])
    return {check.name: check.passed for check in record.checks}


def _passes(answer: str, values: list[GuidelineValue] | None = None) -> bool:
    given = values or []
    return bool(verify(answer, PASSAGES, given, [describe(v) for v in given]).passed)


# --- numbers -------------------------------------------------------------------------------------------------------


def test_number_from_a_cited_passage_passes() -> None:
    assert _passes("The report covers 12 wells [1].")


def test_made_up_number_fails() -> None:
    checks = _checks("The report covers 14 wells [1].")
    assert checks["numbers traced"] is False


def test_number_from_an_uncited_passage_fails() -> None:
    """21 is in passage 2, but the answer cites only passage 1."""
    assert _checks("Report the site within 21 days [1].")["numbers traced"] is False
    assert _passes("Report the site within 21 days [2].")


def test_unit_converted_guideline_value_passes() -> None:
    values = _values("PFOS")
    text = "PFAS NEMP 3.0 compares the sum of PFOS and PFHxS with 70 ng/L [G1]; the current values compare PFOS with "
    for spelling in ("8 ng/L", "0.008 ug/L", "0.008 \u00b5g/L", "0.008 \u03bcg/L", "0.000008 mg/L"):
        assert _passes(f"{text}{spelling} [G2].", values), spelling


def test_a_wrong_concentration_fails_even_when_close() -> None:
    values = _values("PFOS")
    answer = "Under PFAS NEMP 3.0 the sum is compared with 0.07 ug/L [G1], and PFOS alone with 0.08 ug/L [G2]."
    record = verify(answer, PASSAGES, values, [describe(v) for v in values])
    traced = next(c for c in record.checks if c.name == "numbers traced")
    assert not traced.passed
    assert "0.08 ug/L" in traced.detail


def test_concentration_from_passage_text_fails() -> None:
    """0.5 ug/L is in passage 2, but concentrations only ever come from the verified guideline values."""
    checks = _checks("A table lists 0.5 ug/L [2].")
    assert checks["numbers traced"] is False
    assert _checks("A table lists 0.5 [2].")["numbers traced"] is False  # a bare number is not a concentration


def test_percent_must_match_a_percent() -> None:
    assert _passes("Confidence limits are 95% [1].")
    assert _passes("Confidence limits are 95 per cent [1].")
    assert _checks("Confidence limits are 90% [1].")["numbers traced"] is False


def test_section_numbers_and_page_numbers() -> None:
    assert _passes("It is set out in section 9.2.1 [1].")
    assert _passes("See p. 29 of the guideline [1].")
    assert _checks("It is set out in section 9.2.4 [1].")["numbers traced"] is False


def test_guideline_value_metadata_numbers_are_allowed() -> None:
    values = _values("PFOA")
    answer = (
        "PFAS NEMP 3.0 lists PFOA at 0.56 ug/L in Table 4 on PDF page 57 [G1]. The current ADWG values list PFOA "
        "at 0.2 ug/L [G2]."
    )
    assert _passes(answer, values)


def test_read_numbers_units_and_placeholders() -> None:
    found = read_numbers("PFOS 8 ng/L, 0.07 \u00b5g/L, 12 mg/kg, 95 %, 1,000 wells, [CLIENT-1] and [3]")
    assert [(q.kind, str(q.canonical)) for q in found] == [
        ("water", "0.008"),
        ("water", "0.07"),
        ("soil", "12"),
        ("percent", "95"),
        ("plain", "1000"),
    ]


# --- citations and sentences ---------------------------------------------------------------------------------------


def test_citation_to_a_missing_passage_fails() -> None:
    assert _checks("The report covers 12 wells [3].")["citations exist"] is False
    assert _checks("The report covers 12 wells [0].")["citations exist"] is False


def test_citation_to_a_missing_guideline_value_fails() -> None:
    assert _checks("The limit is stated elsewhere [G1].")["citations exist"] is False


def test_answer_with_no_citation_fails() -> None:
    record = verify("A report includes a sampling plan.", PASSAGES, [], [])
    assert record.passed is False
    assert "the answer cites nothing" in record.checks[0].detail


def test_malformed_citation_fails() -> None:
    assert _checks("A report includes a sampling plan [1-2].")["citations exist"] is False


def test_every_sentence_must_be_cited() -> None:
    checks = _checks("A report includes a sampling plan [1]. It also needs a map.")
    assert checks["every sentence cited"] is False
    assert _passes("A report includes a sampling plan [1]. It is reported to DWER [2].")


def test_sentence_splitting() -> None:
    assert sentences("See p. 29 of it [1]. Then e.g. This [2]. Last one.") == [
        "See p. 29 of it [1].",
        "Then e.g. This [2].",
        "Last one.",
    ]
    assert sentences("A sampling plan is needed. [1] It is reported [2].") == [
        "A sampling plan is needed. [1]",
        "It is reported [2].",
    ]


def test_citations_parser() -> None:
    assert citations("a [1][2] b [1, 3] c [G2] d [CLIENT-1] e [1-3]") == ([1, 2, 1, 3], ["G2"], ["[1-3]"])


# --- guard rails in the wording ------------------------------------------------------------------------------------


def test_dashes_fail() -> None:
    assert _checks("A report includes a sampling plan \u2014 and more [1].")["no dashes"] is False
    assert _checks("A report includes a sampling plan \u2013 and more [1].")["no dashes"] is False


@pytest.mark.parametrize(
    "answer",
    [
        "The water is safe to drink [1].",
        "The groundwater is not safe [1].",
        "This site is contaminated [2].",
        "The result fails the guideline [1].",
    ],
)
def test_verdict_wording_fails(answer: str) -> None:
    assert _checks(answer)["no verdict wording"] is False


def test_both_rules_must_be_stated_and_neither_picked() -> None:
    values = _values("PFOS")
    one_rule = "The current ADWG value for PFOS is 0.008 ug/L [G2]."
    assert _checks(one_rule, values)["both rules shown"] is False
    picked = (
        "PFAS NEMP 3.0 compares the sum with 0.07 ug/L [G1] and the current values compare PFOS with 0.008 ug/L "
        "[G2]. Consultants should use the current value [G2]."
    )
    assert _checks(picked, values)["no rule picked"] is False


def test_passing_record_lists_every_check() -> None:
    values = _values("PFOS")
    answer = (
        "Under PFAS NEMP 3.0, PFOS has no value on its own: PFOS and PFHxS are added and compared with 0.07 ug/L "
        "[G1]. Under the current ADWG values, PFOS alone is compared with 8 ng/L [G2]."
    )
    record = verify(answer, PASSAGES, values, [describe(v) for v in values])
    assert record.passed is True
    assert record.summary == "All 7 checks passed."
    assert [c.name for c in record.checks] == [
        "citations exist",
        "every sentence cited",
        "numbers traced",
        "both rules shown",
        "no rule picked",
        "no verdict wording",
        "no dashes",
    ]


# --- routes and values ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the drinking-water limit for PFOS?", ("PFOS",)),
        ("What is the PFOS guideline value?", ("PFOS",)),
        ("What are the guideline values for PFOS and PFHxS in drinking water?", ("PFOS+PFHxS",)),
        ("Drinking water limits for PFOA and PFBS?", ("PFOA", "PFBS")),
        ("What is the soil limit for PFOS?", ()),
        ("What is the PFOS limit for freshwater ecosystems?", ()),
        ("How do I sample PFOS in groundwater?", ()),
        ("What is a conceptual site model?", ()),
    ],
)
def test_drinking_water_route(question: str, expected: tuple[str, ...]) -> None:
    assert drinking_water_analytes(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Is this site contaminated?",
        "Is the groundwater at MB2 safe to drink?",
        "Does PFOS above the guideline mean the site is contaminated?",
        "Are these results dangerous?",
        "is my bore water safe",
    ],
)
def test_verdict_questions(question: str) -> None:
    assert asks_for_verdict(question)


@pytest.mark.parametrize(
    "question",
    [
        "When do I have to report a suspected contaminated site to DWER?",
        "Are there rules for contaminated sites in WA?",
        "What is a conceptual site model?",
        "What is the drinking-water limit for PFOS?",
        "Is clean fill defined in the guidelines?",
    ],
)
def test_not_verdict_questions(question: str) -> None:
    assert not asks_for_verdict(question)


def test_values_come_from_both_rules() -> None:
    pfos = guideline_values(["PFOS"])
    assert [(v.marker, v.rule, v.value, v.available) for v in pfos] == [
        ("G1", "nemp-3.0", "0.07", True),
        ("G2", "current", "0.008", True),
    ]
    assert pfos[0].compared_quantity == "PFOS on its own"
    assert "PFOS on its own" in pfos[0].note  # NEMP 3.0 Table 4 footnote a: PFOS only, PFHxS only, and the sum
    assert "no value for PFOS" not in pfos[0].note
    assert "no separate value" not in pfos[0].note
    assert (pfos[0].table, pfos[0].page, pfos[1].page) == ("Table 4", "57", "49")


def test_missing_values_are_shown_not_skipped() -> None:
    pfbs = guideline_values(["PFBS"])
    assert [(v.rule, v.available, v.value) for v in pfbs] == [("nemp-3.0", False, None), ("current", True, "1")]
    assert "no value" in describe(pfbs[0])
    summed = guideline_values(["PFOS+PFHxS"])
    assert [(v.rule, v.available) for v in summed] == [("nemp-3.0", True), ("current", False)]


def test_tidy_dashes() -> None:
    assert tidy_dashes("pages 10\u201312 \u2014 see below") == "pages 10 to 12, see below"
