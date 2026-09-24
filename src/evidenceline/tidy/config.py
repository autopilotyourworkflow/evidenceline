"""Settings for the QA checks. The defaults follow the sources in :mod:`evidenceline.tidy.sources`; the marginal
factor for blank-linked results is a demonstration setting, not a published rule, and is labelled so in output."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from evidenceline.errors import EvidencelineError
from evidenceline.units import fmt


@dataclass(frozen=True, slots=True)
class QaConfig:
    """Thresholds for the field-duplicate RPD check and for marking a blank-linked result as marginal.

    - ``rpd_review_above``: RPD (%) above which a pair is flagged for review (NEPM B3: "greater than 30%").
    - ``rpd_investigate_above``: RPD (%) above which the flag says "investigate" (upper end of the AS 4482.1
      typical range, as quoted by GHD 2014).
    - ``rpd_no_limit_below_lor_multiple``: when the pair mean is below this many LORs, the RPD is shown with no
      limit applied (GHD 2014 Table 6: "< 10 x limit of reporting (LOR) No limits").
    - ``blank_marginal_factor``: a result linked to a blank detection is marked marginal when it is above a value
      by no more than this factor. A demonstration setting.
    """

    rpd_review_above: Decimal = Decimal(30)
    rpd_investigate_above: Decimal = Decimal(50)
    rpd_no_limit_below_lor_multiple: Decimal = Decimal(10)
    blank_marginal_factor: Decimal = Decimal("1.5")

    def __post_init__(self) -> None:
        if self.rpd_review_above <= 0 or self.rpd_no_limit_below_lor_multiple < 0:
            raise EvidencelineError("RPD thresholds must be positive.")
        if self.rpd_investigate_above < self.rpd_review_above:
            raise EvidencelineError(
                f"The 'investigate' RPD ({fmt(self.rpd_investigate_above)}%) cannot be below the 'review' RPD "
                f"({fmt(self.rpd_review_above)}%)."
            )
        if self.blank_marginal_factor <= 1:
            raise EvidencelineError("The marginal factor must be above 1 (for example 1.5).")


DEFAULT_CONFIG = QaConfig()
