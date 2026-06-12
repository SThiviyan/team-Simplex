from pydantic import BaseModel, ConfigDict, Field


class QueryRow(BaseModel):
    """One search input: a query ID, the name to search, and the country code."""

    query_id: str
    name: str
    jurisdiction: str


class McpServerEntry(BaseModel):
    """One entry in a per-country ranked MCP server list (mocked via CSV)."""

    rank: int
    name: str
    url: str
    notes: str = ""

    @property
    def is_placeholder(self) -> bool:
        # Placeholder entries (no real MCP endpoint yet) are skipped by the agent.
        return "example.invalid" in self.url


class ExtractionPayload(BaseModel):
    """What the LLM returns per attempt — the output schema minus query_id.

    All fields are required-but-nullable and `extra="forbid"` so the generated
    JSON schema is valid for the API's structured-output format (which needs
    `additionalProperties: false` and supports no numeric min/max constraints —
    confidence bounds are enforced in code, not in the schema).
    """

    model_config = ConfigDict(extra="forbid")

    registry_id: str | None = Field(
        description="Official registration number from the relevant registry (e.g. HRB 6684). Null if not found."
    )
    registry_court: str | None = Field(
        description="Specific court or registry office (required for DE, AT, etc. — e.g. 'Amtsgericht München'). Null if unknown."
    )
    name_normalized_register_name: str | None = Field(
        description="Full legal name as registered (e.g. 'Sinpex GmbH', not 'Sinpex'). Null if no match."
    )
    jurisdiction_confirmed: str | None = Field(
        description="Confirmed country or state/province (e.g. 'DE' or 'DE-BY'). Null if not confirmed by the registry evidence."
    )
    confidence: float = Field(
        description="How sure the system is that this is the correct registry entry, in [0, 1]. Used for calibration scoring — be honest, not optimistic."
    )
    source: str | None = Field(
        description="At least one citable URL or registry document reference supporting the answer. Null if no match."
    )
    no_match_reason: str | None = Field(
        description="Only when registry_id is null: 'not_in_registry', 'ambiguous_candidates', 'out_of_scope', or another short snake_case label. Null on a match."
    )

    def clamped_confidence(self) -> float:
        return max(0.0, min(1.0, self.confidence))


class ExtractionResult(BaseModel):
    """Final per-query result row — exactly the agreed CSV output schema."""

    query_id: str
    registry_id: str | None = None
    registry_court: str | None = None
    name_normalized_register_name: str | None = None
    jurisdiction_confirmed: str | None = None
    confidence: float = 0.0
    source: str | None = None
    no_match_reason: str | None = None

    @classmethod
    def from_payload(cls, query_id: str, payload: ExtractionPayload) -> "ExtractionResult":
        return cls(
            query_id=query_id,
            registry_id=payload.registry_id,
            registry_court=payload.registry_court,
            name_normalized_register_name=payload.name_normalized_register_name,
            jurisdiction_confirmed=payload.jurisdiction_confirmed,
            confidence=payload.clamped_confidence(),
            source=payload.source,
            no_match_reason=payload.no_match_reason,
        )


class PipelineRunSummary(BaseModel):
    """Response of POST /api/pipeline/run."""

    rows_processed: int
    output_csv: str
    results: list[ExtractionResult]
