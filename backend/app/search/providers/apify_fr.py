"""France — companies search & SIREN enrich via Apify. Opt-in, scoped FR."""

from app.integrations import apify_fr
from app.search.providers._apify_base import ApifyProvider


class ApifyFrSearchProvider(ApifyProvider):
    name = "apify_fr"
    jurisdictions = {"FR"}
    _integration = apify_fr
