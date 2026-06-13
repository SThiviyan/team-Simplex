"""OpenCorporates global company search via Apify ("Corporate Decision
Intelligence: KYC, Compliance, Supplier Risk"). Opt-in, global, premium.
"""

from app.integrations import apify_opencorporates
from app.search.providers._apify_base import ApifyProvider


class ApifyOpenCorporatesSearchProvider(ApifyProvider):
    name = "opencorporates"
    jurisdictions = None  # global
    tier = "global"
    _integration = apify_opencorporates
