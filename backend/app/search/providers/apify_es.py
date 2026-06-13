"""Spain — Registro Mercantil directory name search via Apify. Opt-in, scoped ES."""

from app.integrations import apify_es
from app.search.providers._apify_base import ApifyProvider


class ApifyEsSearchProvider(ApifyProvider):
    name = "apify_es"
    jurisdictions = {"ES"}
    _integration = apify_es
