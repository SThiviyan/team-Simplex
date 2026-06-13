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
    # The keyless fallback drives a headless browser against handelsregister.de —
    # ~50s per query and frequently returns nothing. Off by default so a missing
    # API key means the provider returns quickly (empty) instead of blocking the
    # whole gather. Set true only if you really want the slow scrape.
    handelsregister_scrape_fallback: bool = Field(
        default=False,
        description="Allow the slow browser scrape of handelsregister.de when no API key is set",
    )

    # --- Gather layer (federated provider fan-out) ------------------------
    # Hard per-provider deadline. _gather awaits every selected provider, so one
    # slow/hanging register would otherwise stall the whole batch. Anything past
    # this is cancelled and treated as an (empty) provider failure — non-fatal.
    provider_timeout: float = Field(
        default=12.0,
        description="Per-provider search timeout (seconds); slow providers are cancelled",
    )

    # --- Companies House (UK commercial register) -------------------------
    # Optional, keyed source. If unset, the provider disables itself and the
    # keyless sources still work. Used as the HTTP Basic username (blank pw).
    # Read from the UK_COMPANY_HOUSE_KEY env var (pydantic matches the field
    # name case-insensitively).
    uk_company_house_key: str | None = Field(
        default=None, description="Companies House REST API key (HTTP Basic username)"
    )

    # --- KVK (Netherlands commercial register) ----------------------------
    # Defaults to KVK's public TEST key (synthetic data, test endpoint) so the
    # provider works out of the box. Set a production key from developers.kvk.nl
    # (paid per query) to switch to the live endpoint and real data.
    kvk_api_key: str | None = Field(
        default="l7xx1f2691f2520d487b902f4e0b57a0b197",
        description="KVK API key (sent as 'apikey'); default is the public test key",
    )

    # --- Pipeline (registry-lookup agent chain) ---
    # The Anthropic SDK reads ANTHROPIC_API_KEY from the environment itself.
    anthropic_model: str = Field(
        default="claude-opus-4-8", description="Model for the Layer-1 agent and the eval step"
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
        default=4,
        description="How many query rows are processed in parallel during a batch run",
    )
    owner_lookup_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in: after a company is matched, web-search its owner and include it "
            "in the output. Off by default because it adds a full web-search LLM call "
            "per match (the main latency cost). Set OWNER_LOOKUP_ENABLED=true to enable."
        ),
    )


settings = Settings()
