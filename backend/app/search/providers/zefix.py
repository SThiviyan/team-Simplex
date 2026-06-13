"""Switzerland — Zefix central business index (zefix.admin.ch). Scoped CH.

Free but token-gated (HTTP Basic). Self-disables without credentials, same as
Companies House / KVK; degrades to [] on error.
"""

from app.config import settings
from app.integrations import zefix
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class ZefixSearchProvider(SearchProvider):
    name = "zefix"
    jurisdictions = {"CH"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not (settings.zefix_user and settings.zefix_password):
            return []  # free but registered: self-disable without credentials
        try:
            rows = await zefix.search_companies(
                query, (settings.zefix_user, settings.zefix_password), limit
            )
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
