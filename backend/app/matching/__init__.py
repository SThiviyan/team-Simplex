"""Matching layer — RapidFuzz gross filter + LLM semantic filter.

This is the *second* layer of the chain. The gather layer
(`app.search.csv_search`) collects registry records for a `(name, jurisdiction)`
input; this layer takes those records **in memory** (no JSON file in between),
runs a cheap RapidFuzz pre-filter, then asks Claude to pick the single winning
candidate.

Public entry point: `app.matching.pipeline.run_matching`.
"""

from app.matching.pipeline import run_matching

__all__ = ["run_matching"]
