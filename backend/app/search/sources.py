"""The canonical list of search providers (sources).

One place that builds every provider, used by the API (main.py) and the unified
MCP server so they always agree on which sources exist and which jurisdictions
each covers.
"""

from app.search.base import SearchProvider
from app.search.providers.annuaire import AnnuaireSearchProvider
from app.search.providers.brreg import BrregSearchProvider
from app.search.providers.cro import CroSearchProvider
from app.search.providers.cvr import CvrSearchProvider
from app.search.providers.gleif import GleifSearchProvider
from app.search.providers.handelsregister import HandelsregisterSearchProvider
from app.search.providers.prh import PrhSearchProvider
from app.search.providers.sec import SecSearchProvider
from app.search.providers.wikidata import WikidataSearchProvider


def all_providers() -> list[SearchProvider]:
    """Global providers (GLEIF, Wikidata) plus the jurisdiction-scoped registers."""
    return [
        GleifSearchProvider(),  # global (LEI)
        WikidataSearchProvider(),  # global (SPARQL)
        HandelsregisterSearchProvider(),  # DE
        BrregSearchProvider(),  # NO
        AnnuaireSearchProvider(),  # FR
        CvrSearchProvider(),  # DK
        CroSearchProvider(),  # IE
        SecSearchProvider(),  # US
        PrhSearchProvider(),  # FI
    ]
