from pydantic import BaseModel, ConfigDict, Field


class QueryRow(BaseModel):
    """One search input: a query ID, the name to search, and the country code."""

    query_id: str
    name: str
    jurisdiction: str


class McpServerEntry(BaseModel):
    """One entry in a per-country ranked MCP server list.

    `url` is either an external MCP endpoint (`https://...`, optionally with an
    auth token) or one of our own per-country endpoints (`internal:<bucket>`,
    reached over the in-memory transport).
    """

    rank: int
    name: str
    url: str
    notes: str = ""
    auth_token: str = ""

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
    # --- Tier A datapoints (fill ONLY from tool-result evidence; null if absent) ---
    registered_address: str | None = Field(
        description="Registered address as the sources report it (street, postcode, city, country). Null if no source showed it."
    )
    incorporation_date: str | None = Field(
        description="Date the company was REGISTERED/INCORPORATED with the register (NOT the founding/establishment year). ISO YYYY-MM-DD (or YYYY if only the year is known). Null if no source showed it."
    )
    organization_type: str | None = Field(
        description="Legal form as registered (e.g. GmbH, AG, Ltd, B.V., S.à r.l.). Null if no source showed it."
    )
    status: str | None = Field(
        description="Company status: active / dissolved / in_liquidation / dormant. Null if no source showed it."
    )
    # --- Tier B (partial coverage — fill only when a source explicitly lists them) ---
    officers: str | None = Field(
        description="Officers/directors/legal representatives as 'role: name; role: name'. Null if no source listed any."
    )
    reasoning: str = Field(
        description="One or two sentences: why this confidence — what evidence supports (or fails to support) the match."
    )

    def clamped_confidence(self) -> float:
        return max(0.0, min(1.0, self.confidence))


class EnrichmentPayload(BaseModel):
    """Layer-2 web-fill output schema: only the Tier A/B gap fields, all
    nullable, `extra="forbid"` for the structured-output format."""

    model_config = ConfigDict(extra="forbid")

    registry_court: str | None = Field(
        description="The specific register court or registry office that issued the registration (e.g. 'Amtsgericht München', 'Greffe du Tribunal de Commerce de Rouen'). Null if not verifiable."
    )
    registered_address: str | None = Field(
        description="Registered/legal address as 'street, postcode, city, country'. Null if not verifiable."
    )
    incorporation_date: str | None = Field(
        description="Registration/incorporation date with the register (NOT founding/establishment year). ISO YYYY-MM-DD (or YYYY). Null if not verifiable."
    )
    organization_type: str | None = Field(
        description="Legal form as registered (GmbH, Ltd, B.V., ...). Null if not verifiable."
    )
    status: str | None = Field(
        description="active / dissolved / in_liquidation / dormant. Null if not verifiable."
    )
    officers: str | None = Field(
        description="'role: name; role: name' for explicitly listed officers/directors. Null if none found."
    )
    source: str | None = Field(
        description="One URL supporting the filled values. Null if nothing was filled."
    )
    reasoning: str = Field(description="One sentence: what was found where, or why fields stay null.")


class ExtractionResult(BaseModel):
    """Final per-query result row — exactly the agreed CSV output schema.

    Field order here IS the CSV column order (csv_io derives RESULT_COLUMNS
    from it): the required minimum schema first, then Tier A, Tier B, and the
    abstention/calibration columns.
    """

    # Truth-table column order: required minimum, Tier A, confidence_flag —
    # then our extras (officers, numeric confidence) — and source LAST.
    query_id: str
    registry_id: str | None = None
    registry_court: str | None = None
    name_normalized_register_name: str | None = None
    jurisdiction_confirmed: str | None = None
    no_match_reason: str | None = None
    registered_address: str | None = None
    incorporation_date: str | None = None
    organization_type: str | None = None
    status: str | None = None
    confidence_flag: str | None = None  # verified | probable | ambiguous | not_found | error
    officers: str | None = None  # Tier B
    confidence: float = 0.0
    source: str | None = None

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
            registered_address=payload.registered_address,
            incorporation_date=payload.incorporation_date,
            organization_type=payload.organization_type,
            status=payload.status,
            officers=payload.officers,
        )


class PipelineRunSummary(BaseModel):
    """Response of POST /api/pipeline/run."""

    run_id: str
    rows_processed: int
    output_csv: str
    results: list[ExtractionResult]
