"""Evidenceline: trace every number in a PFAS monitoring paragraph to its lab row or guideline table."""

from evidenceline.core import check_paragraph, compare_rules, get_results, lookup_limit
from evidenceline.errors import EvidencelineError

__all__ = ["EvidencelineError", "check_paragraph", "compare_rules", "get_results", "lookup_limit"]
__version__ = "0.1.0"
