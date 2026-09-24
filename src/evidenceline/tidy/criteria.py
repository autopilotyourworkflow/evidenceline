"""Investigation levels the QA checks compare against, and exact bounds for single results and sums.

Soil values come from the site folder's ``soil_criteria.json`` (HIL A, from the verified criteria set). Water values
come from the packaged ``guidelines.json`` (both drinking-water rules). A value is an investigation level, not a
finding about the site. For water there are two rules and the checks always report both.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from importlib.resources.abc import Traversable
from typing import Any, Literal, cast

from evidenceline.dataset import default_dataset
from evidenceline.errors import EvidencelineError
from evidenceline.tidy.records import LabRow
from evidenceline.tidy.units import CANONICAL, Matrix, parse_unit
from evidenceline.units import fmt, parse_decimal

Status = Literal["above", "not above", "not confirmed"]

RULE_NAMES: dict[str, str] = {
    "nemp-3.0": "PFAS NEMP 3.0 (named by WA as adopted)",
    "current": "the current national values (NEMP 3.1, ADWG updated 2025)",
}


@dataclass(frozen=True, slots=True)
class Criterion:
    """One value to compare with. ``members`` are lab analyte names; more than one means a sum."""

    id: str
    matrix: Matrix
    scenario: str
    label: str
    members: tuple[str, ...]
    each_member_too: bool
    value: Decimal
    rule: str | None
    citation: str
    note: str

    @property
    def unit(self) -> str:
        return CANONICAL[self.matrix]

    @property
    def is_sum(self) -> bool:
        return len(self.members) > 1

    def shown(self) -> str:
        return f"{fmt(self.value)} {self.unit}"

    def named(self) -> str:
        """For example ``the drinking-water value for PFOS (0.008 ug/L) under the current national values``."""
        rule = f" under {RULE_NAMES.get(self.rule, self.rule)}" if self.rule else ""
        return f"the {self.scenario} value for {self.label} ({self.shown()}){rule}"


@dataclass(frozen=True, slots=True)
class Comparison:
    """A result or a sum compared with a criterion, with the exact arithmetic."""

    criterion: Criterion
    quantity: str
    parts: tuple[LabRow, ...]
    lower: Decimal
    upper: Decimal

    @property
    def status(self) -> Status:
        if self.lower > self.criterion.value:
            return "above"
        if self.upper <= self.criterion.value:
            return "not above"
        return "not confirmed"

    def against(self) -> str:
        """What the quantity is compared with; notes when a sum's value is applied to one member alone."""
        alone = self.criterion.is_sum and len(self.parts) == 1
        return self.criterion.named() + (", which also applies to each alone" if alone else "")

    @property
    def has_non_detect(self) -> bool:
        return any(not part.detected for part in self.parts)

    def arithmetic(self) -> str:
        """``0.0022 + 0.0012 = 0.0034 mg/kg`` or, with a non-detect, ``between 0.0015 and 0.0017 mg/kg``."""
        unit = self.criterion.unit
        if len(self.parts) == 1:
            return self.parts[0].shown_canonical()
        terms = " + ".join(fmt(part.value) if part.value is not None else f"<{fmt(part.lor)}" for part in self.parts)
        if self.lower == self.upper:
            return f"{terms} = {fmt(self.lower)} {unit}"
        return f"{terms}: between {fmt(self.lower)} and {fmt(self.upper)} {unit} (a non-detect counts as 0 to its LOR)"


def compare(criterion: Criterion, results: Mapping[str, LabRow]) -> list[Comparison]:
    """Compare one sample's results with ``criterion``. Returns nothing if a member was not analysed."""
    if any(member not in results for member in criterion.members):
        return []
    parts = tuple(results[member] for member in criterion.members)
    found = [
        Comparison(
            criterion=criterion,
            quantity=criterion.label if criterion.is_sum else parts[0].analyte,
            parts=parts,
            lower=sum((part.bound()[0] for part in parts), Decimal(0)),
            upper=sum((part.bound()[1] for part in parts), Decimal(0)),
        )
    ]
    if criterion.is_sum and criterion.each_member_too:
        for part in parts:
            low, high = part.bound()
            found.append(Comparison(criterion=criterion, quantity=part.analyte, parts=(part,), lower=low, upper=high))
    return found


def ratio(comparison: Comparison) -> Decimal:
    """Measured value (lower bound) divided by the criterion, to two decimal places."""
    return (comparison.lower / comparison.criterion.value).quantize(Decimal("0.01"))


def load_soil_criteria(path: Traversable) -> tuple[tuple[Criterion, ...], str]:
    """Read ``soil_criteria.json``: the criteria and the scenario label."""
    try:
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        scenario = str(payload["scenario_short"])
        criteria: list[Criterion] = []
        for raw in cast(list[dict[str, Any]], payload["criteria"]):
            unit = parse_unit(str(raw["unit"]))
            criteria.append(
                Criterion(
                    id=str(raw["id"]),
                    matrix="soil",
                    scenario=scenario,
                    label=str(raw["label"]),
                    members=tuple(str(member) for member in cast(list[str], raw["members"])),
                    each_member_too=bool(raw["each_member_too"]),
                    value=unit.to_canonical(parse_decimal(str(raw["value"]))),
                    rule=None,
                    citation=f"{raw['document']}, {raw['table']}, {raw['page']}",
                    note=str(raw["note"]),
                )
            )
        return tuple(criteria), str(payload["scenario"])
    except (KeyError, ValueError, TypeError) as exc:
        raise EvidencelineError(f"soil_criteria.json could not be read: {exc}") from exc


def water_criteria() -> tuple[Criterion, ...]:
    """Both drinking-water rules from the packaged ``guidelines.json``.

    A value for the sum of PFOS and PFHxS (PFAS NEMP 3.0 Table 4) also applies to PFOS alone and to PFHxS alone:
    footnote a reads "this means concentrations of PFOS only, PFHxS only, and the sum of the two". The soil
    criteria say the same in their own file (Table 5, footnote a)."""
    criteria: list[Criterion] = []
    for rule in default_dataset().rules.values():
        for limit in rule.limits:
            criteria.append(
                Criterion(
                    id=f"{rule.id}:{limit.key}",
                    matrix="water",
                    scenario=f"{limit.scenario.replace(' ', '-')}",
                    label=limit.label,
                    members=limit.members,
                    each_member_too=limit.applies_to == "sum",
                    value=limit.value,
                    rule=rule.id,
                    citation=rule.citation,
                    note=rule.wa_status,
                )
            )
    return tuple(criteria)
