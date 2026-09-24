"""check_paragraph: the deterministic checker."""

from __future__ import annotations

import pytest

from evidenceline import check_paragraph
from evidenceline.dataset import Dataset
from evidenceline.errors import EvidencelineError
from evidenceline.models import CheckedItem, ParagraphCheck

from .conftest import WALKTHROUGH_RIGHT, WALKTHROUGH_WRONG


def items(result: ParagraphCheck, kind: str) -> list[CheckedItem]:
    return [item for item in result.checked if item.kind == kind]


def only(result: ParagraphCheck, kind: str) -> CheckedItem:
    found = items(result, kind)
    assert len(found) == 1, found
    return found[0]


# --- the two walkthrough sentences ------------------------------------------------------------------------------


def test_walkthrough_draft_sentence_is_flagged() -> None:
    result = check_paragraph(WALKTHROUGH_WRONG, "MB2")

    change = only(result, "change")
    assert change.status == "inconsistent"
    assert change.quote == "increased"
    assert "went from 0.041 ug/L on 19 November 2024 to 0.038 ug/L on 16 September 2025" in change.explanation
    assert "a fall of 0.003 ug/L" in change.explanation
    assert [(s.file, s.rows) for s in change.sources] == [("mb2_round2_lab.csv", [14]), ("mb2_round3_lab.csv", [14])]

    guideline = only(result, "guideline")
    assert guideline.status == "depends_on_rule"
    outcomes = {o.rule: o.holds for o in guideline.rule_outcomes}
    assert outcomes == {"nemp-3.0": False, "current": True}
    assert "0.038 + 0.019 = 0.057" in guideline.explanation
    assert "holds under current only" in guideline.explanation
    assert "'Now' was read as 16 September 2025" in guideline.explanation
    assert "A later round exists (5 May 2026)" in guideline.explanation

    assert result.needs_attention == 2
    assert result.not_checked == []
    assert [item.kind for item in result.checked] == ["change", "guideline"]  # in reading order


def test_walkthrough_corrected_sentence_passes() -> None:
    result = check_paragraph(WALKTHROUGH_RIGHT, "MB2")
    change = only(result, "change")
    assert change.status == "consistent"
    assert change.quote == "lower"
    assert "0.038 ug/L on 16 September 2025 and 0.041 ug/L on 19 November 2024" in change.explanation
    assert "0.003 ug/L (7.3%) lower on 16 September 2025" in change.explanation
    assert "'Slightly' is a judgement word" in change.explanation
    assert result.needs_attention == 0
    assert result.not_checked == []
    assert len(result.checked) == 1


def test_output_always_lists_checked_and_not_checked_and_never_says_no_issues() -> None:
    for text in (WALKTHROUGH_RIGHT, WALKTHROUGH_WRONG, "The site should be remediated."):
        result = check_paragraph(text, "MB2")
        dumped = result.model_dump_json().lower()
        assert "no issues" not in dumped
        assert "not checked" in result.summary.lower()
        assert result.scope
        assert result.sentences


def test_no_em_or_en_dashes_in_any_output() -> None:
    texts = [
        WALKTHROUGH_WRONG,
        WALKTHROUGH_RIGHT,
        "PFOS was 0.039 ug/L in September 2025. PFAS rose in 2025. PFOA was not detected in any round.",
    ]
    for text in texts:
        dumped = check_paragraph(text, "MB2").model_dump_json()
        assert chr(0x2014) not in dumped  # em dash
        assert chr(0x2013) not in dumped  # en dash


# --- (a) numbers --------------------------------------------------------------------------------------------------


def test_exact_lab_value_traces_to_its_row() -> None:
    item = only(check_paragraph("PFOS was 0.038 ug/L in September 2025.", "MB2"), "number")
    assert item.status == "consistent"
    assert item.sources[0].kind == "lab result"
    assert (item.sources[0].lab_report, item.sources[0].file, item.sources[0].rows) == (
        "SYN-250916",
        "mb2_round3_lab.csv",
        [14],
    )


@pytest.mark.parametrize(
    "written", ["38 ng/L", "0.038 µg/L", "0.038 μg/L", "0.038 ug/l", "0.000038 mg/L", "0.0380 ug/L"]
)
def test_units_and_precision_are_normalised_before_tracing(written: str) -> None:
    item = only(check_paragraph(f"PFOS was {written} in September 2025.", "MB2"), "number")
    assert item.status == "consistent", item.explanation
    assert item.sources[0].value == "0.038"


def test_eight_ng_per_litre_traces_to_the_current_pfos_limit() -> None:
    item = only(check_paragraph("The PFOS guideline is 8 ng/L.", "MB2"), "number")
    assert item.status == "consistent"
    assert item.sources[0].kind == "guideline limit"
    assert item.sources[0].rule == "current"
    assert item.sources[0].value == "0.008"


def test_untraced_number_is_flagged_with_the_nearest_value() -> None:
    item = only(check_paragraph("PFOS was 0.039 ug/L in September 2025.", "MB2"), "number")
    assert item.status == "untraced"
    assert "Nearest lab value: PFOS 0.038 ug/L, 16 September 2025" in item.explanation


def test_number_that_belongs_to_another_analyte_is_flagged() -> None:
    item = only(check_paragraph("PFOS was 0.019 ug/L in September 2025.", "MB2"), "number")
    assert item.status == "inconsistent"
    assert "PFHxS 0.019 ug/L" in item.explanation


def test_number_that_belongs_to_another_date_is_flagged() -> None:
    item = only(check_paragraph("PFOS was 0.041 ug/L in September 2025.", "MB2"), "number")
    assert item.status == "inconsistent"
    assert "19 November 2024" in item.explanation


def test_computed_sum_and_difference_trace() -> None:
    result = check_paragraph(
        "In September 2025 the sum of PFOS and PFHxS was 0.057 ug/L. "
        "PFOS fell by 3 ng/L between November 2024 and September 2025.",
        "MB2",
    )
    numbers = items(result, "number")
    assert [n.status for n in numbers] == ["consistent", "consistent"]
    assert numbers[0].sources[0].kind == "computed sum"
    assert numbers[0].sources[0].rows == [14, 15]
    assert numbers[1].sources[0].kind == "computed difference"


def test_detection_limit_traces_for_a_less_than_value() -> None:
    item = only(check_paragraph("PFOA was <0.001 ug/L in September 2025.", "MB2"), "number")
    assert item.status == "consistent"
    assert item.sources[0].kind == "detection limit"


def test_numbers_without_units_are_listed_as_not_checked() -> None:
    result = check_paragraph("PFOS was 0.038 in September 2025.", "MB2")
    assert result.checked == []
    assert any(n.quote == "0.038" and "without a concentration unit" in n.reason for n in result.not_checked)


# --- (b) change claims --------------------------------------------------------------------------------------------


def test_correct_decrease_claim_passes() -> None:
    item = only(check_paragraph("PFOS decreased between November 2024 and September 2025.", "MB2"), "change")
    assert item.status == "consistent"


@pytest.mark.parametrize(
    "verb", ["increased", "rose", "went up", "decreased", "fell", "went down", "dropped", "declined"]
)
def test_change_verbs(verb: str) -> None:
    item = only(check_paragraph(f"PFOS {verb} between November 2024 and September 2025.", "MB2"), "change")
    expected = "consistent" if verb in {"decreased", "fell", "went down", "dropped", "declined"} else "inconsistent"
    assert item.status == expected


@pytest.mark.parametrize(
    ("sentence", "status"),
    [
        ("PFOS did not increase between November 2024 and September 2025.", "consistent"),
        ("PFOS has not increased between November 2024 and September 2025.", "consistent"),
        ("PFOS didn't rise between November 2024 and September 2025.", "consistent"),
        ("PFOS did not decrease between November 2024 and September 2025.", "inconsistent"),
        ("PFHxS did not increase between November 2024 and September 2025.", "inconsistent"),
        ("There was no increase in PFOS between November 2024 and September 2025.", "consistent"),
    ],
)
def test_negation(sentence: str, status: str) -> None:
    item = only(check_paragraph(sentence, "MB2"), "change")
    assert item.status == status, item.explanation


def test_negation_quote_includes_the_negation() -> None:
    item = only(check_paragraph("PFOS did not increase between November 2024 and September 2025.", "MB2"), "change")
    assert item.quote == "not increase"


def test_negation_does_not_leak_across_clauses() -> None:
    result = check_paragraph("PFOS did not increase and PFHxS fell between March 2024 and May 2026.", "MB2")
    statuses = [(i.quote, i.status) for i in items(result, "change")]
    assert statuses == [("not increase", "consistent"), ("fell", "consistent")]


@pytest.mark.parametrize(
    "dates",
    [
        ("November 2024", "September 2025"),
        ("Nov 2024", "Sep 2025"),
        ("Nov 2024", "Sept 2025"),
        ("Nov. 2024", "Sept. 2025"),
        ("19 November 2024", "16 September 2025"),
        ("19 Nov 2024", "16 Sep 2025"),
        ("November 19, 2024", "September 16, 2025"),
        ("2024-11-19", "2025-09-16"),
        ("19th November 2024", "16th September 2025"),
    ],
)
def test_date_forms(dates: tuple[str, str]) -> None:
    first, second = dates
    item = only(check_paragraph(f"PFOS fell between {first} and {second}.", "MB2"), "change")
    assert item.status == "consistent"
    assert "19 November 2024" in item.explanation
    assert "16 September 2025" in item.explanation


def test_dates_given_in_reverse_order_are_compared_chronologically_for_verbs() -> None:
    item = only(check_paragraph("PFOS fell between September 2025 and November 2024.", "MB2"), "change")
    assert item.status == "consistent"


def test_comparative_order_follows_than() -> None:
    higher = only(check_paragraph("PFOS was higher in November 2024 than in September 2025.", "MB2"), "change")
    lower = only(check_paragraph("PFOS was lower in November 2024 than in September 2025.", "MB2"), "change")
    assert higher.status == "consistent"
    assert lower.status == "inconsistent"


def test_comparative_without_than_is_not_checked() -> None:
    result = check_paragraph("PFOS was lower in November 2024 and September 2025.", "MB2")
    assert items(result, "change") == []
    assert any("does not say which is compared" in n.reason for n in result.not_checked)


def test_since_compares_with_the_latest_round() -> None:
    item = only(check_paragraph("PFOS has decreased since March 2024.", "MB2"), "change")
    assert item.status == "consistent"
    assert "latest round, 5 May 2026" in item.explanation


def test_sum_change() -> None:
    item = only(
        check_paragraph("The sum of PFOS and PFHxS fell between November 2024 and September 2025.", "MB2"), "change"
    )
    assert item.status == "consistent"
    assert "0.059 ug/L" in item.explanation
    assert "0.057 ug/L" in item.explanation


def test_similar_and_stable_need_a_person_unless_identical() -> None:
    stable = only(check_paragraph("PFOS was stable between November 2024 and September 2025.", "MB2"), "change")
    assert stable.status == "needs_judgement"
    assert "scientist's call" in stable.explanation
    not_changed = only(
        check_paragraph("PFOS has not changed significantly between November 2024 and September 2025.", "MB2"),
        "change",
    )
    assert not_changed.status == "needs_judgement"
    assert "statistical test" in not_changed.explanation


def test_each_analyte_in_a_coordinated_subject_is_checked() -> None:
    result = check_paragraph("PFOS and PFHxS both decreased between November 2024 and September 2025.", "MB2")
    assert [(i.status, "PFHxS" in i.explanation) for i in items(result, "change")] == [
        ("consistent", False),
        ("inconsistent", True),  # PFHxS went from 0.018 to 0.019
    ]


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        ("PFOS increased between September 2025 and October 2025.", "no MB2 sampling round in October 2025"),
        ("PFOS increased in September 2025.", "Only one date is given"),
        ("PFOS increased.", "No dates are given"),
        ("PFAS rose between November 2024 and September 2025.", "'PFAS' names a group of chemicals"),
        ("It rose between November 2024 and September 2025.", "No analyte is named"),
        ("PFOA fell between November 2024 and September 2025.", "not detected"),
    ],
)
def test_change_claims_it_cannot_read_are_not_checked(sentence: str, reason: str) -> None:
    result = check_paragraph(sentence, "MB2")
    assert items(result, "change") == []
    assert any(reason in n.reason for n in result.not_checked), result.not_checked


# --- (c) guideline and threshold claims -------------------------------------------------------------------------


def test_named_rule_is_used() -> None:
    nemp = only(check_paragraph("PFOS was above the NEMP 3.0 guideline in September 2025.", "MB2"), "guideline")
    adwg = only(check_paragraph("PFOS was above the ADWG guideline in September 2025.", "MB2"), "guideline")
    assert nemp.status == "inconsistent"
    assert adwg.status == "consistent"


def test_under_the_rule_is_not_read_as_below() -> None:
    result = check_paragraph("PFOS is above the limit under the current guideline in September 2025.", "MB2")
    guideline = only(result, "guideline")
    assert guideline.status == "consistent"


def test_below_and_within_claims() -> None:
    below = only(check_paragraph("PFOS was below the guideline in May 2026.", "MB2"), "guideline")
    within = only(check_paragraph("PFHxS was within the guideline on 16 September 2025.", "MB2"), "guideline")
    assert below.status == "consistent"
    assert within.status == "consistent"


def test_negated_guideline_claim() -> None:
    item = only(check_paragraph("PFOS did not exceed the NEMP 3.0 guideline in November 2024.", "MB2"), "guideline")
    assert item.status == "consistent"
    assert item.quote.startswith("not exceed")


def test_all_rounds_claim() -> None:
    item = only(check_paragraph("PFOS exceeded the ADWG guideline in all monitoring rounds.", "MB2"), "guideline")
    assert item.status == "inconsistent"  # 5 May 2026: 0.006 is not above 0.008


def test_sum_claim_under_current_rule_is_not_applicable() -> None:
    item = only(check_paragraph("The sum of PFOS and PFHxS was above the guideline in March 2024.", "MB2"), "guideline")
    outcomes = {o.rule: o.holds for o in item.rule_outcomes}
    assert outcomes == {"nemp-3.0": True, "current": None}
    assert item.status == "depends_on_rule"


def test_equal_to_the_limit_is_not_above(boundary_data: Dataset) -> None:
    above = only(
        check_paragraph("PFOS was above the ADWG guideline in January 2025.", "MB9", data=boundary_data), "guideline"
    )
    within = only(
        check_paragraph("PFOS was within the ADWG guideline in January 2025.", "MB9", data=boundary_data), "guideline"
    )
    below = only(
        check_paragraph("PFOS was below the ADWG guideline in January 2025.", "MB9", data=boundary_data), "guideline"
    )
    assert (above.status, within.status, below.status) == ("inconsistent", "consistent", "inconsistent")
    assert "equals 0.008 ug/L, so it is not above it" in above.explanation


def test_sum_equal_to_the_nemp_limit_is_not_above(boundary_data: Dataset) -> None:
    item = only(
        check_paragraph(
            "The sum of PFOS and PFHxS was above the NEMP 3.0 guideline in January 2025.", "MB9", data=boundary_data
        ),
        "guideline",
    )
    assert item.status == "inconsistent"
    assert "0.008 + 0.062 = 0.07" in item.explanation


def test_threshold_claim_with_a_stated_number_uses_strict_comparison() -> None:
    equal = only(check_paragraph("PFOS was above 0.038 ug/L in September 2025.", "MB2"), "guideline")
    below = only(check_paragraph("PFOS was below 0.008 ug/L in May 2026.", "MB2"), "guideline")
    assert equal.status == "inconsistent"
    assert below.status == "consistent"


def test_other_guideline_scenarios_are_not_checked() -> None:
    result = check_paragraph("PFOS was above the recreational guideline in September 2025.", "MB2")
    assert items(result, "guideline") == []
    assert any("Only drinking-water guideline values" in n.reason for n in result.not_checked)


def test_sentence_naming_both_rules_is_not_checked() -> None:
    result = check_paragraph("In March 2024 PFOS was above both the NEMP 3.0 and ADWG values.", "MB2")
    assert items(result, "guideline") == []
    assert any("names both rules" in n.reason for n in result.not_checked)


def test_over_the_period_is_not_a_guideline_claim() -> None:
    result = check_paragraph("PFOS fell over the monitoring period and is now below the guideline.", "MB2")
    guideline = only(result, "guideline")
    assert guideline.quote == "below the guideline"
    assert guideline.status == "consistent"
    assert "latest round with data, 5 May 2026" in guideline.explanation


# --- (d) detection claims -----------------------------------------------------------------------------------------


def test_detection_claims() -> None:
    right = only(check_paragraph("PFOA was not detected in any round.", "MB2"), "detection")
    wrong = only(check_paragraph("PFOS was not detected in May 2026.", "MB2"), "detection")
    lor = only(check_paragraph("PFBS was below the LOR in September 2025.", "MB2"), "detection")
    assert (right.status, wrong.status, lor.status) == ("consistent", "inconsistent", "consistent")
    assert right.quote == "not detected"


# --- scope, sentences and bad input -------------------------------------------------------------------------------


def test_other_wells_and_unreadable_dates_are_not_checked() -> None:
    result = check_paragraph(
        "PFOS in MB3 rose between November 2024 and September 2025. PFOS was 0.006 ug/L on 05/05/2026. "
        "PFOS rose in 2025. PFOS fell between 31 February 2025 and May 2026.",
        "MB2",
    )
    reasons = [n.reason for n in result.not_checked]
    assert any("does not exist on the calendar" in r for r in reasons)
    assert any("names well MB3" in r for r in reasons)
    assert any("Numeric dates" in r for r in reasons)
    assert any("A year on its own" in r for r in reasons)


def test_sentence_without_claims_is_listed_as_not_checked() -> None:
    result = check_paragraph("The site should be remediated.", "MB2")
    assert result.checked == []
    assert len(result.not_checked) == 1
    assert "No numbers with a unit" in result.not_checked[0].reason


def test_multi_sentence_paragraph_is_split_and_numbered() -> None:
    text = f"{WALKTHROUGH_RIGHT} PFOS was 0.038 ug/L in Sept. 2025. {WALKTHROUGH_WRONG}"
    result = check_paragraph(text, "MB2")
    assert len(result.sentences) == 3
    assert sorted({i.sentence for i in result.checked}) == [1, 2, 3]
    assert result.needs_attention == 2


@pytest.mark.parametrize(("text", "well", "message"), [("  ", "MB2", "empty"), ("PFOS fell.", "MB8", "Unknown well")])
def test_bad_input(text: str, well: str, message: str) -> None:
    with pytest.raises(EvidencelineError, match=message):
        check_paragraph(text, well)


# --- dates implied by quoted lab values ---------------------------------------------------------------------------


def test_dates_are_taken_from_quoted_lab_values_when_none_are_written() -> None:
    wrong = only(check_paragraph("PFOS increased from 0.006 ug/L to 0.062 ug/L.", "MB2"), "change")
    assert wrong.status == "inconsistent"  # 0.062 was March 2024 and 0.006 was May 2026: a fall over time
    assert "lab values quoted" in wrong.explanation
    right = only(check_paragraph("PFOS decreased from 0.041 ug/L to 0.038 ug/L.", "MB2"), "change")
    assert right.status == "consistent"


def test_guideline_claim_uses_the_round_of_a_quoted_sum() -> None:
    result = check_paragraph(
        "The combined concentration of PFOS and PFHxS (0.057 ug/L) was below the PFAS NEMP 3.0 drinking water "
        "criterion of 0.07 ug/L.",
        "MB2",
    )
    guideline = only(result, "guideline")
    assert guideline.status == "consistent"
    assert "16 September 2025 was taken from the lab value quoted" in guideline.explanation
    assert "the sum of PFOS and PFHxS" in guideline.explanation


def test_since_with_words_between_and_remains_means_latest_round() -> None:
    since = only(check_paragraph("PFOS has generally decreased since monitoring began in March 2024.", "MB2"), "change")
    assert since.status == "consistent"
    remains = only(check_paragraph("PFOS remains above the current guideline.", "MB2"), "guideline")
    assert remains.status == "inconsistent"  # 5 May 2026: 0.006 is not above 0.008
    assert "'Remains' was read as the latest round with data, 5 May 2026" in remains.explanation


def test_down_from_and_up_from_with_quoted_values() -> None:
    down = check_paragraph("PFOS was 0.038 ug/L in September 2025, down from 0.041 ug/L.", "MB2")
    assert only(down, "change").status == "consistent"
    assert [n.status for n in items(down, "number")] == ["consistent", "consistent"]  # 0.041 is a reference value
    up = check_paragraph("PFOS was 0.038 ug/L in September 2025, up from 0.041 ug/L.", "MB2")
    assert only(up, "change").status == "inconsistent"


def test_comparative_with_one_written_date_and_a_quoted_value() -> None:
    result = check_paragraph("PFHxS was 0.019 ug/L, slightly higher than in November 2024 (0.018 ug/L).", "MB2")
    change = only(result, "change")
    assert change.status == "consistent"
    assert "0.001 ug/L (5.6%) higher on 16 September 2025" in change.explanation
    assert [n.status for n in items(result, "number")] == ["consistent", "consistent"]


def test_date_in_another_clause_still_constrains_a_number() -> None:
    item = only(check_paragraph("In September 2025, PFOS was 0.041 ug/L.", "MB2"), "number")
    assert item.status == "inconsistent"


def test_between_two_dates_keeps_both_dates_for_a_guideline_claim() -> None:
    item = only(
        check_paragraph("PFOS was above the guideline between November 2024 and September 2025.", "MB2"), "guideline"
    )
    assert item.quote == "above the guideline"
    assert "19 November 2024, 16 September 2025" in item.explanation
    assert {o.rule: o.holds for o in item.rule_outcomes} == {"nemp-3.0": False, "current": True}


def test_guideline_claim_uses_the_date_in_its_own_clause() -> None:
    result = check_paragraph(
        "PFOS exceeded the ADWG value of 8 ng/L in Sep 2025, and was higher than in November 2024.", "MB2"
    )
    guideline = only(result, "guideline")
    assert guideline.quote == "exceeded the ADWG value"
    assert "on 16 September 2025." in guideline.explanation
    assert only(result, "change").status == "inconsistent"  # 0.038 in Sep 2025 is lower than 0.041 in Nov 2024


# --- what a quoted number is (added after the adversarial review) ---------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "status"),
    [
        ("The NEMP 3.0 criterion for the sum of PFOS and PFHxS is 0.07 ug/L.", "consistent"),
        ("The ADWG value for PFOS is 8 ng/L.", "consistent"),
        ("The ADWG value for PFOS is 0.07 ug/L.", "inconsistent"),  # 0.07 is the NEMP 3.0 sum value
        ("PFOS decreased by 0.024 ug/L between March 2024 and September 2025.", "consistent"),
        ("PFOS decreased by 0.024 ug/L between November 2024 and September 2025.", "inconsistent"),
        ("The sum was 0.057 ug/L in September 2025.", "consistent"),
        ("PFOS in MB2 was 0.041 ug/L in November 2024 and is now 0.041 ug/L.", "inconsistent"),
    ],
)
def test_number_role_decides_what_it_may_trace_to(sentence: str, status: str) -> None:
    last = items(check_paragraph(sentence, "MB2"), "number")[-1]
    assert last.status == status, last.explanation


def test_values_after_a_list_of_analytes_are_not_guessed() -> None:
    result = check_paragraph("PFOS and PFHxS were 0.038 ug/L and 0.019 ug/L in September 2025.", "MB2")
    assert items(result, "number") == []
    assert [n.quote for n in result.not_checked] == ["0.038 ug/L", "0.019 ug/L"]
    assert all("not clear which one" in n.reason for n in result.not_checked)


def test_unit_slip_hint_points_at_the_lab_value() -> None:
    item = only(check_paragraph("PFOS was 0.006 mg/L in May 2026.", "MB2"), "number")
    assert item.status == "untraced"
    assert "Check the unit" in item.explanation
    assert "PFOS 0.006 ug/L, 5 May 2026" in item.explanation


def test_judgement_and_analyte_comparison_reasons_are_specific() -> None:
    judgement = check_paragraph("The groundwater at MB2 is unsafe to drink.", "MB2")
    assert any("judgement about contamination, safety or risk" in n.reason for n in judgement.not_checked)
    between = check_paragraph("PFOS was higher than PFHxS in May 2026.", "MB2")
    assert any("two analytes rather than two dates" in n.reason for n in between.not_checked)


@pytest.mark.parametrize(
    ("sentence", "statuses"),
    [
        # fill_numbers output in a natural wording: the rule's name before "value" marks a guideline, not a result
        ("PFHxS was 0.009 ug/L, below the current value of 0.03 ug/L.", ["consistent", "consistent"]),
        (
            "In May 2026 PFHxS was 0.009 ug/L, against the current drinking-water value of 0.03 ug/L.",
            ["consistent", "consistent"],
        ),
        ("In September 2025 PFOS was 0.038 ug/L against 0.008 ug/L under the current rule.", ["consistent"] * 2),
        ("In September 2025 PFOS was 0.038 ug/L, below the current value of 0.05 ug/L.", ["consistent", "untraced"]),
        (
            "In September 2025 PFOS was 0.041 ug/L, above the current value of 0.008 ug/L.",
            ["inconsistent", "consistent"],
        ),
        # "current value of PFOS" with no comparison word still means the latest result
        ("The current value of PFOS at MB2 is 0.006 ug/L.", ["consistent"]),
    ],
)
def test_current_value_after_a_comparison_is_a_guideline(sentence: str, statuses: list[str]) -> None:
    assert [i.status for i in items(check_paragraph(sentence, "MB2"), "number")] == statuses
