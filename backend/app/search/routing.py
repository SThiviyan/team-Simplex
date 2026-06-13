"""Jurisdiction- and cost-aware source selection.

With many providers to choose from, this ranks the relevant ones so a caller (the
MCP server / an agent) picks the right calls: free official registers first, then
free global identifiers, then premium (paid Apify) sources. Number-only sources
are dropped when the query carries no identifier (they could not match anyway).
"""

import re

from app.search.base import SearchProvider

# Lower sorts first (preferred).
_TIER_RANK = {"register": 0, "global": 1, "enrichment": 2}
_COST_RANK = {"free": 0, "premium": 1}


def covers(provider: SearchProvider, jurisdiction: str | None) -> bool:
    """True if the provider is relevant to the jurisdiction (global or matching)."""
    if provider.jurisdictions is None:
        return True
    if not jurisdiction:
        return True
    return jurisdiction.upper() in provider.jurisdictions


def has_identifier(query: str | None) -> bool:
    """Does the query plausibly carry a registration/VAT/CNPJ/KRS number?

    Strips the separators registration numbers use (spaces, dots, slashes,
    hyphens) so e.g. a CNPJ '33.000.167/0001-01' reads as a long digit run.
    """
    if not query:
        return False
    return bool(re.search(r"\d{6,}", re.sub(r"[\s.\-/]", "", query)))


def rank_providers(
    providers: list[SearchProvider],
    jurisdiction: str | None,
    *,
    include_premium: bool = True,
    identifier_present: bool = True,
) -> list[SearchProvider]:
    """Relevant providers for ``jurisdiction``, best-first.

    - excludes premium sources when ``include_premium`` is False;
    - excludes number-only sources when ``identifier_present`` is False;
    - sorts free-before-premium, register < global < enrichment, then by name.
    """
    selected = [
        p
        for p in providers
        if covers(p, jurisdiction)
        and (include_premium or p.cost != "premium")
        and (identifier_present or p.lookup != "number")
    ]
    selected.sort(
        key=lambda p: (_COST_RANK.get(p.cost, 0), _TIER_RANK.get(p.tier, 0), p.name)
    )
    return selected


def describe(provider: SearchProvider) -> dict:
    """Routing-relevant metadata for one provider."""
    return {
        "name": provider.name,
        "jurisdictions": sorted(provider.jurisdictions) if provider.jurisdictions else None,
        "tier": provider.tier,
        "cost": provider.cost,
        "lookup": provider.lookup,
    }
