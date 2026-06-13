"""Japan — gBizINFO company data name search via Apify. Opt-in, scoped JP."""

from app.integrations import apify_jp_gbiz
from app.search.providers._apify_base import ApifyProvider


class ApifyJpSearchProvider(ApifyProvider):
    name = "apify_jp_gbiz"
    jurisdictions = {"JP"}
    _integration = apify_jp_gbiz
