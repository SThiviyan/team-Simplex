from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, model_validator

from app.search.registry_format import (
    infer_jurisdiction,
    normalize_date,
    normalize_registry,
    normalize_status,
)


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
    # Date the company was incorporated/first registered (ISO YYYY-MM-DD).
    incorporation_date: str | None = None
    # Entity status, normalised to a common vocabulary (active, in_liquidation,
    # dissolved, …); see registry_format.normalize_status.
    status: str | None = None
    # Per-source signals the resolver can rank on, e.g. {"sitelinks": 87}.
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _conform_registry_to_national_standard(self) -> "SearchResult":
        """Reshape registry_id / registry_court to the jurisdiction's standard
        form (e.g. DE -> "HRB 42243" / "Amtsgericht München"), regardless of which
        provider produced them. Conservative: unrecognised values pass through."""
        # Trust what the entry itself says about its country (registered address,
        # then description) over the server it was found on — e.g. a company
        # returned by the French register whose address is "München, Allemagne" is
        # DE, not FR. Fall back to the provider's jurisdiction when the entry
        # states no country. Done first so the registry_id is formatted for the
        # corrected jurisdiction.
        self.jurisdiction = infer_jurisdiction(self.address, self.snippet) or self.jurisdiction
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

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        ...
