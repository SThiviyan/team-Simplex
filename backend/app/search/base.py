from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


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
    # Per-source signals the resolver can rank on, e.g. {"sitelinks": 87}.
    metadata: dict = Field(default_factory=dict)


class SearchProvider(ABC):
    name: str
    # ISO 3166-1 alpha-2 codes this provider covers. None = global (always
    # relevant). The resolver uses this to skip e.g. a German register when
    # the requested jurisdiction is Hungary.
    jurisdictions: set[str] | None = None

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        ...
