"""Brazil — CNPJ (Receita Federal) lookup via Apify. Opt-in, scoped BR, by number."""

from app.integrations import apify_br
from app.search.providers._apify_base import ApifyProvider


class ApifyBrSearchProvider(ApifyProvider):
    name = "apify_br"
    jurisdictions = {"BR"}
    lookup = "number"
    _integration = apify_br
