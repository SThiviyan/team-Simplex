"""SUPERSEDED by app/matching/company_matcher.py (RapidFuzz gross filter).

Kept for reference only — the unified pipeline (runner.py) routes the
"Python Per-processing" box through the matching layer instead.
"""

from app.pipeline.models import ExtractionResult


def prefilter(results: list[ExtractionResult]) -> list[ExtractionResult]:
    """Drop empty candidates, dedupe by registry_id, best confidence first."""
    kept: list[ExtractionResult] = []
    seen_ids: set[str] = set()

    for r in sorted(results, key=lambda r: r.confidence, reverse=True):
        if not r.registry_id and not r.no_match_reason:
            continue  # neither data nor an explanation — useless candidate
        if r.registry_id and r.registry_id in seen_ids:
            continue
        if r.registry_id:
            seen_ids.add(r.registry_id)
        kept.append(r)

    # TODO: extend filtering rules here (name-similarity scoring, jurisdiction
    # plausibility checks, blocklists, ...).
    return kept
