"""One-shot recursive retry for the matching layer.

A low-confidence candidate is never returned. When the matching layer cannot
confidently identify the entity it instead emits ``decision='recursive_search'``
with a ``suggested_query`` — a name it judges MORE likely to surface the right
registry record than the original, while still denoting the SAME entity with high
confidence (e.g. an expanded acronym or legal form).

This module acts on that signal: for each such winner it re-queries the registers
ONCE with the suggested name and re-runs the match. It accepts the retry only if
it produces a *high-confidence* match (>= ``MIN_MATCH_CONFIDENCE``); otherwise the
query resolves to ``no_match``. It never recurses more than once, and it never
returns the original low-confidence candidate.

The retry is skipped in mock/offline mode (no LLM to reason about a better query).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.matching.pipeline import run_matching
from app.matching.semantic_filter import (
    DECISION_MATCH,
    DECISION_NO_MATCH,
    DEFAULT_MODEL,
    MIN_MATCH_CONFIDENCE,
)
from app.search.base import SearchProvider
from app.search.csv_search import _to_record, search_jurisdiction

logger = logging.getLogger(__name__)


def _no_match(winner: dict[str, Any], reasoning: str) -> dict[str, Any]:
    """Resolve a winner to no_match, dropping any candidate / suggestion."""
    return {
        **winner,
        "decision": DECISION_NO_MATCH,
        "winning_candidate": None,
        "confidence": 0.0,
        "recursive_search": None,
        "reasoning": reasoning,
    }


async def _gather_and_match(
    providers: list[SearchProvider],
    name: str,
    jurisdiction: str | None,
    *,
    limit: int,
    model: str,
    mock: bool,
) -> dict[str, Any]:
    """Re-query the registers for one refined name and run the two-layer match.

    No JSON file is written (unlike ``csv_search``) — this is a focused retry.
    """
    results, _called, _skipped = await search_jurisdiction(providers, name, jurisdiction, limit)
    records = [_to_record("retry", name, r) for r in results]
    records = [r for r in records if r.get("name_normalized_register_name")]
    return await asyncio.to_thread(
        run_matching, records, name, jurisdiction or "", model=model, mock=mock
    )


async def apply_recursive_retry(
    providers: list[SearchProvider],
    winners: list[dict[str, Any]],
    *,
    limit: int = 25,
    model: str = DEFAULT_MODEL,
    mock: bool = False,
) -> list[dict[str, Any]]:
    """Resolve every winner, performing at most one recursive retry each.

    - ``recursive_search`` winner with a confidently same-entity suggestion ->
      re-query once; keep the retry only if it is a high-confidence match.
    - any leftover low-confidence ``match`` -> downgraded to no_match.
    - everything else passes through unchanged.

    In mock mode the input is returned untouched (offline, no LLM reasoning).
    """
    if mock:
        return winners

    async def _resolve(winner: dict[str, Any]) -> dict[str, Any]:
        decision = winner.get("decision")
        rs = winner.get("recursive_search") or {}
        suggested = (rs.get("suggested_query") or "").strip()

        if decision != "recursive_search" or not suggested:
            # Safety net: a low-confidence match that slipped through is dropped.
            if decision == DECISION_MATCH and (winner.get("confidence") or 0.0) < MIN_MATCH_CONFIDENCE:
                return _no_match(winner, "Best candidate below the confidence floor; not returned.")
            return winner

        # The refined query must still denote the original entity with high
        # confidence. For an LLM suggestion that is its decision confidence; the
        # deterministic abbreviation-expansion path sets `link_confidence`.
        link_conf = rs.get("link_confidence", winner.get("confidence") or 0.0)
        if link_conf < MIN_MATCH_CONFIDENCE:
            return _no_match(
                winner,
                f"Re-query '{suggested}' not confidently the same entity "
                f"(link {link_conf:.2f}); not attempted.",
            )

        jurisdiction = winner.get("jurisdiction")
        try:
            retry = await _gather_and_match(
                providers, suggested, jurisdiction, limit=limit, model=model, mock=mock
            )
        except Exception as exc:  # a failed retry must never break the response
            logger.warning("recursive retry for %r failed: %s", suggested, exc)
            return _no_match(winner, f"Retry with '{suggested}' failed: {exc}")

        if retry.get("decision") == DECISION_MATCH and (
            retry.get("confidence") or 0.0
        ) >= MIN_MATCH_CONFIDENCE:
            # Accept the retry, but keep the row tied to the ORIGINAL query.
            return {
                "query_id": winner.get("query_id"),
                "name": winner.get("name"),
                "jurisdiction": jurisdiction,
                "decision": DECISION_MATCH,
                "winning_candidate": retry.get("winning_candidate"),
                "confidence": retry.get("confidence"),
                "reasoning": (
                    f"Original query was inconclusive; re-queried as '{suggested}' "
                    f"and found a confident match. {retry.get('reasoning', '')}"
                ).strip(),
                "recursive_search": None,
                "references": retry.get("references", []),
                "candidates": retry.get("candidates", []),
                # Provenance: the refined query that actually produced the result.
                "retried_query": suggested,
            }

        return _no_match(
            winner,
            f"Re-queried as '{suggested}' but found no confident match; not returned.",
        )

    return list(await asyncio.gather(*(_resolve(w) for w in winners)))
