"""EU VAT (VIES) validation/enrichment via Apify. Opt-in, by VAT number.

Scoped to the EU member states; resolves a VAT number in the query to the
official registered name/address.
"""

from app.integrations import apify_vat
from app.search.providers._apify_base import ApifyProvider

_EU = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}


class ApifyVatSearchProvider(ApifyProvider):
    name = "apify_eu_vat"
    jurisdictions = _EU
    tier = "enrichment"
    lookup = "number"
    _integration = apify_vat
