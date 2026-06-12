from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    title: str
    url: str | None = None
    snippet: str
    score: float = Field(ge=0.0, le=1.0)
    source: str


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        ...
