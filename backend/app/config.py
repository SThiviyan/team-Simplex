from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = Field(default=8080, description="Cloud Run injects PORT=8080")

    # --- Handelsregister.ai (German commercial register) ------------------
    # Optional, keyed source. If unset, the provider disables itself and the
    # keyless GLEIF + Wikidata sources still work.
    handelsregister_api_key: str | None = Field(
        default=None, description="handelsregister.ai API key (sent as x-api-key)"
    )

    # --- Optional keyed register sources (each provider disables itself when
    # its key is unset, so the keyless stack keeps working) -------------------
    uk_company_house_key: str | None = Field(
        default=None, description="Companies House REST API key (free, HTTP Basic username)"
    )
    nzbn_api_key: str | None = Field(
        default=None, description="NZBN API subscription key (Ocp-Apim-Subscription-Key)"
    )
    ajpes_user: str | None = Field(default=None, description="AJPES restPrsInfo username")
    ajpes_password: str | None = Field(default=None, description="AJPES restPrsInfo password")
    ajpes_schema: str | None = Field(default=None, description="AJPES authorised data-set code")

    # --- Pipeline (registry-lookup agent chain) ---
    # The Anthropic SDK reads ANTHROPIC_API_KEY from the environment itself.
    anthropic_model: str = Field(
        default="claude-opus-4-8", description="Model for the Layer-1 research agent"
    )
    matching_model: str = Field(
        default="claude-sonnet-4-6",
        description="Model for the matching-layer semantic filter — a constrained forced-tool "
        "classification where a faster tier holds up (gated on the practice-set score)",
    )
    pipeline_mock: bool = Field(
        default=False,
        description="True = no API calls; deterministic stub results (offline dev/tests)",
    )
    confidence_threshold: float = Field(
        default=0.8,
        description="Layer-1 agent stops walking the MCP list once a result reaches this confidence",
    )
    pipeline_concurrency: int = Field(
        default=20,
        description="How many query rows are processed in parallel during a batch run. Rows are "
        "independent, so wall clock ≈ slowest row once this reaches the batch size; lower it if "
        "API rate limits / 529 storms bite",
    )


settings = Settings()
