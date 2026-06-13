"""The canonical list of search providers (sources).

One place that builds every provider, used by the API (main.py) and the unified
MCP server so they always agree on which sources exist and which jurisdictions
each covers.
"""

from app.search.base import SearchProvider
from app.search.providers.ajpes import AjpesSearchProvider
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
from app.search.providers.nzbn import NzbnSearchProvider
from app.search.providers.orgbook_ca import OrgbookCaSearchProvider
from app.search.providers.prh import PrhSearchProvider
from app.search.providers.rasham_il import RashamIlSearchProvider
from app.search.providers.rpo_sk import RpoSkSearchProvider
from app.search.providers.rsk_is import RskIsSearchProvider
from app.search.providers.sec import SecSearchProvider
from app.search.providers.ur_lv import UrLvSearchProvider
from app.search.providers.wikidata import WikidataSearchProvider
from app.search.providers.zefix import ZefixSearchProvider


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
        CompaniesHouseSearchProvider(),  # GB (keyed; self-disables without key)
        AresSearchProvider(),  # CZ
        AjpesSearchProvider(),  # SI (keyed; self-disables without credentials)
        NzbnSearchProvider(),  # NZ (keyed; self-disables without key)
        # --- pulled from the MCP branch (wider data intake) ------------------
        KvkNlSearchProvider(),  # NL (keyed; ships KVK public test key by default)
        AriregisterSearchProvider(),  # EE (keyless)
        RpoSkSearchProvider(),  # SK (keyless)
        OrgbookCaSearchProvider(),  # CA / British Columbia (keyless)
        RashamIlSearchProvider(),  # IL (keyless)
        RskIsSearchProvider(),  # IS (keyless; scraped)
        UrLvSearchProvider(),  # LV (keyless)
        # --- pulled from verify_info: keyless national registers -------------
        KrsPlSearchProvider(),  # PL — KRS (keyless, api-krs.ms.gov.pl)
        BrasilSearchProvider(),  # BR — CNPJ (keyless, brasilapi.com.br)
        GbizInfoSearchProvider(),  # JP — gBizINFO (keyed free token; self-disables)
        ZefixSearchProvider(),  # CH — Zefix (keyed free registration; self-disables)
        # --- Apify actor-backed (premium; all self-disable without APIFY_API_KEY
        # + APIFY_ENABLED). NorthData is the keyed path to DE/AT/CH register data
        # that bypasses handelsregister.de's IP block. -----------------------
        NorthDataSearchProvider(),  # DE / AT / CH (NorthData)
        ApifyUsSearchProvider(),  # US (state-level)
        ApifyKrsSearchProvider(),  # PL (KRS via Apify)
        ApifyKvkSearchProvider(),  # NL (KVK via Apify)
        ApifyEsSearchProvider(),  # ES
        ApifyFrSearchProvider(),  # FR
        ApifyBrSearchProvider(),  # BR
        ApifyJpSearchProvider(),  # JP
        ApifyVatSearchProvider(),  # EU VAT (VIES)
        ApifyOpenCorporatesSearchProvider(),  # global (OpenCorporates)
    ]
