"""End-to-end matching: RapidFuzz gross filter -> LLM semantic filter.

This is the in-memory entry point for the second layer of the chain. The gather
layer (``app.search.csv_search``) produces registry records for a
``(name, jurisdiction)`` input; ``run_matching`` takes those records **directly**
(no JSON file in between), runs the cheap local RapidFuzz pre-filter
(``company_matcher``), then hands the surviving shortlist to the LLM semantic
layer (``semantic_filter``) for the final winning decision.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.matching.company_matcher import (
    CONFIDENCE_FIELD,
    Target,
    candidate_to_dict,
    find_candidates,
)
from app.matching.corroboration import corroborate, corroboration_boost
from app.matching.owner_lookup import find_owner
from app.matching.semantic_filter import (
    DECISION_MATCH,
    DEFAULT_MODEL,
    SemanticFilterError,
    semantic_filter,
)

logger = logging.getLogger(__name__)

# Field names on a winning-candidate record (emitted by the gather layer).
_NAME_FIELD = "name_normalized_register_name"
_JURISDICTION_FIELD = "jurisdiction_confirmed"


def run_matching(
    records: list[dict[str, Any]],
    name: str,
    jurisdiction: str,
    *,
    top_n: int = 5,
    score_cutoff: float = 0.5,
    model: str = DEFAULT_MODEL,
    mock: bool = False,
) -> dict[str, Any]:
    """Run the two-layer match over gathered registry ``records`` in memory.

    Parameters
    ----------
    records:
        The registry-record dicts gathered by the search layer for one input row.
    name / jurisdiction:
        The original ``(name, jurisdiction)`` the user searched for. These come
        straight from the initial input — not re-read from any file.
    top_n:
        Max candidates RapidFuzz passes to the LLM (0 = all survivors).
    score_cutoff:
        RapidFuzz name-score gate, 0..1. Lower it to let aliases through.
    model:
        Claude model id for the semantic layer.
    mock:
        Skip the LLM call and deterministically pick the top fuzzy candidate.

    Returns
    -------
    dict
        ``{decision, winning_candidate, confidence, reasoning, recursive_search,
        candidates}``. ``winning_candidate`` (when present) is a full record dict
        — including the enriched ``address`` / ``last_update`` /
        ``organization_type`` fields carried through from the gather layer.
    """
    target = Target(name=name, jurisdiction=jurisdiction)

    # ---- Layer 0: graph consolidation — merge duplicates, count "fame" ---
    # FuzzyAI-style: cluster records that refer to the same entity (graph
    # connected components) into one most-complete row carrying how many distinct
    # sources found it.
    records = corroborate(records)

    # ---- Layer 1: RapidFuzz + Fellegi-Sunter gross filter ----------------
    fuzz_hits = find_candidates(records, target, top_n=top_n, score_cutoff=score_cutoff)
    candidates = [candidate_to_dict(c) for c in fuzz_hits]

    # Fame boost: lift confidence for entities corroborated by many sources, so a
    # mainstream result wins even when the input differs from the registered name
    # (e.g. "siemens" -> "Siemens Aktiengesellschaft"). Then re-rank.
    for cand in candidates:
        provider_count = cand.get("_provider_count", 1)
        base = cand.get(CONFIDENCE_FIELD, 0.0)
        boosted = corroboration_boost(base, provider_count)
        cand[CONFIDENCE_FIELD] = boosted
        cand["_match"]["base_confidence"] = base
        cand["_match"]["confidence_boost"] = round(boosted - base, 4)
        cand["_match"]["fame"] = cand.get("_fame", 1)
        cand["_match"]["provider_count"] = provider_count
        cand["_match"]["completeness"] = cand.get("_completeness", 0.0)
    candidates.sort(
        key=lambda c: (
            c.get(CONFIDENCE_FIELD, 0.0),
            c["_match"].get("provider_count", 0),
            c["_match"].get("completeness", 0.0),
        ),
        reverse=True,
    )

    # ---- Layer 2: LLM semantic filter (calls Claude, unless mock) --------
    try:
        result = semantic_filter(name, jurisdiction, candidates, model=model, mock=mock)
    except SemanticFilterError as exc:
        # The chain must always produce a usable answer for the frontend. If the
        # LLM layer fails (no key, timeout, rate-limit, …), fall back to the top
        # RapidFuzz candidate rather than returning nothing.
        logger.warning("semantic_filter failed (%s); falling back to top fuzzy candidate", exc)
        if candidates:
            top = dict(candidates[0])
            result = {
                "decision": DECISION_MATCH,
                "winning_candidate": top,
                "confidence": float(top.get("confidence") or 0.0),
                "reasoning": f"Semantic filter unavailable ({exc}); used top RapidFuzz candidate.",
                "recursive_search": None,
            }
        else:
            result = {
                "decision": "no_match",
                "winning_candidate": None,
                "confidence": 0.0,
                "reasoning": f"Semantic filter unavailable ({exc}); no fuzzy candidates.",
                "recursive_search": None,
            }

    # Expose the shortlist that fed the decision (useful for the UI / debugging).
    result["candidates"] = candidates
    return result


async def match_payload(
    payload: dict[str, Any],
    *,
    top_n: int = 5,
    score_cutoff: float = 0.5,
    model: str = DEFAULT_MODEL,
    mock: bool = False,
    owner_lookup: bool = True,
) -> list[dict[str, Any]]:
    """Run the matching layer for every query row in a gather-layer ``payload``.

    ``payload`` is the in-memory output of ``app.search.csv_search`` — it carries
    the ``queries`` (each with its ``name`` / ``jurisdiction``) and the gathered
    ``results`` records (joined back via ``query_id``). For each query row we run
    the two-layer match over only that row's records and return one winner entry.

    The blocking semantic-filter call is offloaded to a worker thread so the
    event loop stays free; rows are processed concurrently.

    Returns one dict per query row::

        {
          "query_id", "name", "jurisdiction",
          "decision", "winning_candidate", "confidence", "reasoning",
          "recursive_search", "candidates"
        }
    """
    queries = payload.get("queries") or []
    records = payload.get("results") or []

    # Bucket the gathered records by the query they belong to.
    by_query: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        by_query.setdefault(rec.get("query_id"), []).append(rec)

    async def _one(query: dict[str, Any]) -> dict[str, Any]:
        qid = query.get("query_id")
        name = query.get("name") or ""
        jurisdiction = query.get("jurisdiction") or ""
        rows = by_query.get(qid, [])
        # Drop synthetic "no match" placeholder rows (no registered name).
        rows = [r for r in rows if r.get("name_normalized_register_name")]
        result = await asyncio.to_thread(
            run_matching,
            rows,
            name,
            jurisdiction,
            top_n=top_n,
            score_cutoff=score_cutoff,
            model=model,
            mock=mock,
        )
        # Once a result is determined (a confirmed match), web-search its owner
        # and attach it. Only for real matches; non-matches carry owner=None.
        owner = None
        winner = result.get("winning_candidate")
        if owner_lookup and result.get("decision") == DECISION_MATCH and winner:
            owner = await asyncio.to_thread(
                find_owner,
                winner.get(_NAME_FIELD) or name,
                winner.get(_JURISDICTION_FIELD) or jurisdiction,
                registry_id=winner.get("registry_id"),
                model=model,
                mock=mock,
            )
        result["owner"] = owner
        return {"query_id": qid, "name": name, "jurisdiction": jurisdiction, **result}

    return list(await asyncio.gather(*(_one(q) for q in queries)))
