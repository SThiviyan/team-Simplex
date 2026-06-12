"""Shared helper for national-register providers.

They all wrap a keyless (or self-disabling keyed) integration that returns
normalised row dicts, and map those rows to SearchResults the same way.
"""

from app.search.base import SearchResult


def rows_to_results(rows: list[dict], source: str, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for i, r in enumerate(rows[:limit]):
        name = r.get("name")
        if not name:
            continue
        num = r.get("number")
        snippet = " · ".join(
            str(x) for x in (r.get("legal_form"), r.get("city"), r.get("status"), r.get("country")) if x
        )
        results.append(
            SearchResult(
                title=f"{name} ({num})" if num else name,
                url=r.get("url"),
                snippet=snippet,
                score=round(max(0.4, 0.95 - i * 0.05), 4),
                source=source,
                jurisdiction=r.get("country"),
                registry_id=str(num) if num else None,
                registry_court=r.get("court"),
                register_name=name,
                organization_type=r.get("legal_form"),
                status=r.get("status"),
                incorporation_date=r.get("incorporation_date"),
                address=r.get("address"),
            )
        )
    return results
