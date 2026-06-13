"""The canonical list of search providers (sources).

One place that builds every provider, used by the API (main.py) and the unified
MCP server so they always agree on which sources exist and which jurisdictions
each covers.
"""

from app.search.base import SearchProvider
from app.search.providers.annuaire import AnnuaireSearchProvider
from app.search.providers.apify_br import ApifyBrSearchProvider
from app.search.providers.apify_es import ApifyEsSearchProvider
from app.search.providers.apify_fr import ApifyFrSearchProvider
from app.search.providers.apify_jp import ApifyJpSearchProvider
from app.search.providers.apify_krs import ApifyKrsSearchProvider
from app.search.providers.apify_kvk import ApifyKvkSearchProvider
from app.search.providers.apify_opencorporates import ApifyOpenCorporatesSearchProvider
from app.search.providers.apify_us import ApifyUsSearchProvider
from app.search.providers.apify_vat import ApifyVatSearchProvider
from app.search.providers.ares import AresSearchProvider
from app.search.providers.ariregister import AriregisterSearchProvider
from app.search.providers.brasil import BrasilSearchProvider
from app.search.providers.brreg import BrregSearchProvider
from app.search.providers.companies_house import CompaniesHouseSearchProvider
from app.search.providers.cro import CroSearchProvider
from app.search.providers.cvr import CvrSearchProvider
from app.search.providers.gbizinfo import GbizInfoSearchProvider
from app.search.providers.gleif import GleifSearchProvider
from app.search.providers.handelsregister import HandelsregisterSearchProvider
from app.search.providers.krs_pl import KrsPlSearchProvider
from app.search.providers.kvk_nl import KvkNlSearchProvider
from app.search.providers.northdata import NorthDataSearchProvider
from app.search.providers.orgbook_ca import OrgbookCaSearchProvider
from app.search.providers.rasham_il import RashamIlSearchProvider
from app.search.providers.prh import PrhSearchProvider
from app.search.providers.rpo_sk import RpoSkSearchProvider
from app.search.providers.rsk_is import RskIsSearchProvider
from app.search.providers.sec import SecSearchProvider
from app.search.providers.wikidata import WikidataSearchProvider


def all_providers() -> list[SearchProvider]:
    """Global providers (GLEIF, Wikidata) plus the jurisdiction-scoped registers."""
    return [
        GleifSearchProvider(),  # global (LEI)
        WikidataSearchProvider(),  # global (SPARQL)
        HandelsregisterSearchProvider(),  # DE
        CompaniesHouseSearchProvider(),  # GB
        BrregSearchProvider(),  # NO
        AnnuaireSearchProvider(),  # FR
        CvrSearchProvider(),  # DK
        CroSearchProvider(),  # IE
        SecSearchProvider(),  # US
        PrhSearchProvider(),  # FI
        AresSearchProvider(),  # CZ
        AriregisterSearchProvider(),  # EE
        RpoSkSearchProvider(),  # SK
        OrgbookCaSearchProvider(),  # CA (British Columbia)
        RashamIlSearchProvider(),  # IL
        RskIsSearchProvider(),  # IS (scraped)
        KvkNlSearchProvider(),  # NL
        BrasilSearchProvider(),  # BR (Receita Federal CNPJ via BrasilAPI)
        GbizInfoSearchProvider(),  # JP (gBizINFO / METI, token-gated)
        KrsPlSearchProvider(),  # PL (KRS court register, by KRS number)
        ApifyKrsSearchProvider(),  # PL (KRS rich data via Apify actor, key-gated)
        NorthDataSearchProvider(),  # DACH (NorthData name search via Apify, opt-in)
        ApifyKvkSearchProvider(),  # NL (KvK Handelsregister name search via Apify, opt-in)
        ApifyUsSearchProvider(),  # US (state business-entity name search via Apify, opt-in)
        ApifyFrSearchProvider(),  # FR (companies search & SIREN enrich via Apify, opt-in)
        ApifyJpSearchProvider(),  # JP (gBizINFO company data via Apify, opt-in)
        ApifyBrSearchProvider(),  # BR (CNPJ Receita Federal via Apify, opt-in)
        ApifyEsSearchProvider(),  # ES (Registro Mercantil directory via Apify, opt-in)
        ApifyVatSearchProvider(),  # EU (VAT/VIES validation via Apify, opt-in)
        ApifyOpenCorporatesSearchProvider(),  # global (OpenCorporates via Apify, opt-in)
    ]
