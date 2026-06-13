from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, model_validator

# Country codes people commonly type vs. the ISO 3166-1 alpha-2 codes the
# registries actually report. Without this, "Tesco, UK" filters out every GB
# record and returns nothing.
COUNTRY_ALIASES = {"UK": "GB", "EL": "GR"}


def normalize_country(code: str | None) -> str | None:
    """Uppercase + resolve common aliases (UK -> GB). None/blank stays None."""
    if not code or not code.strip():
        return None
    cc = code.strip().upper()
    return COUNTRY_ALIASES.get(cc, cc)


class SearchResult(BaseModel):
    title: str
    url: str | None = None
    snippet: str
    score: float = Field(ge=0.0, le=1.0)
    source: str
    # ISO 3166-1 alpha-2 country code of the entity, when known (used to filter
    # by jurisdiction). None when the source doesn't report a country.
    jurisdiction: str | None = None
    # Structured registry data (for the output schema). When the source is a
    # company register these carry the official identifier, the issuing court /
    # office, and the full registered legal name.
    registry_id: str | None = None
    registry_court: str | None = None
    register_name: str | None = None
    # Extra entity context surfaced to the final output / frontend, when the
    # source reports it. None when the source doesn't.
    last_update: str | None = None       # when the source's data was last updated
    address: str | None = None           # registered address of the company
    organization_type: str | None = None  # legal form / organization type (e.g. GmbH)
    status: str | None = None            # company status as the source reports it
    incorporation_date: str | None = None  # registration/founding date when reported
    # Further Tier A datapoints, when the source reports them.
    vat_number: str | None = None        # VAT / USt-IdNr / TVA
    trade_names: str | None = None       # trading / other names ('name; name')
    industry_code: str | None = None     # NACE/NAF/SIC/WZ code
    industry: str | None = None          # industry / sector name
    capitalization: str | None = None    # registered/share capital
    business_purpose: str | None = None  # registered business object/purpose
    # Per-source signals the resolver can rank on, e.g. {"sitelinks": 87}.
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _conform_registry_to_national_standard(self) -> "SearchResult":
        """Reshape registry_id / registry_court to the jurisdiction's standard
        form (e.g. DE -> "HRB 42243" / "Amtsgericht München"), regardless of
        which provider produced them. Conservative: unrecognised values pass
        through unchanged (see registry_format)."""
        from app.search.registry_format import (
            infer_jurisdiction,
            normalize_date,
            normalize_registry,
            normalize_status,
        )

        # Trust what the entry itself says about its country (registered
        # address, then description) over the server it was found on — e.g. a
        # company returned by the French register whose address ends in
        # "München, DE" is DE, not FR. Done first so the registry_id is
        # formatted for the corrected jurisdiction. BUT never downgrade a
        # state/province code (US-CA, CA-ON) to the bare country of the same
        # nation — that granularity is what the US/Canada filing number is
        # scoped to.
        inferred = infer_jurisdiction(self.address, self.snippet)
        if inferred:
            cur = (self.jurisdiction or "").upper()
            same_country = cur.split("-")[0] == inferred.upper().split("-")[0]
            if not (same_country and "-" in cur and "-" not in inferred):
                self.jurisdiction = inferred
        self.registry_id, self.registry_court = normalize_registry(
            self.jurisdiction, self.registry_id, self.registry_court
        )
        self.status = normalize_status(self.status)
        self.incorporation_date = normalize_date(self.incorporation_date)
        return self


class SearchProvider(ABC):
    name: str
    # ISO 3166-1 alpha-2 codes this provider covers. None = global (always
    # relevant). The resolver uses this to skip e.g. a German register when
    # the requested jurisdiction is Hungary.
    jurisdictions: set[str] | None = None
    # Hard gather-layer timeout (seconds). None = use settings.provider_timeout.
    # Override (higher) for inherently slow sources like the Handelsregister
    # browser scrape, which the default fast-API timeout would strangle.
    search_timeout: float | None = None

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        ...
