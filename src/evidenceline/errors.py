"""Errors raised for bad input. Messages are written to be read by a person or by Claude."""


class EvidencelineError(ValueError):
    """An input Evidenceline cannot use, such as an unknown well, analyte, rule or date."""
