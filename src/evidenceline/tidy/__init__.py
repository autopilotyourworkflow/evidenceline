"""Tidy lab results: parse lab, chain-of-custody and field-sheet files, reconcile sample ids, normalise units and
run QA checks, each with its rule and source. Deterministic; no language model is involved.

Entry points: :func:`evidenceline.tidy.engine.tidy_lab_files`, :func:`evidenceline.tidy.engine.get_review_item`,
and :func:`evidenceline.tidy.tools.register_tools` for the MCP server.
"""
