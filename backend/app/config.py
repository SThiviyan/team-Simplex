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
    # KVK ships a public TEST key (synthetic data); a production key returns real
    # Dutch companies. Default to the test key so the NL provider works out of the
    # box; set a prod key in secrets for live data, or "" to disable.
    kvk_api_key: str | None = Field(
        default="l7xx1f2691f2520d487b902f4e0b57a0b197",
        description="KVK Zoeken API key (default = KVK public test key; prod key for real data)",
    )
    # gBizINFO (Japan, METI): free but token-gated. Provider self-disables unset.
    gbizinfo_api_token: str | None = Field(
        default=None, description="gBizINFO API token (sent as X-hojinInfo-api-token)"
    )
    # Zefix (Switzerland): free Public REST API but registration-gated (HTTP Basic).
    # Register at https://www.zefix.admin.ch; provider self-disables without both.
    zefix_user: str | None = Field(default=None, description="Zefix Public REST username")
    zefix_password: str | None = Field(default=None, description="Zefix Public REST password")

    # --- Apify (actor-backed premium sources: NorthData DE/AT/CH, US, KRS, …) --
    # Optional. Every Apify-backed provider requires BOTH a token AND the global
    # apify_enabled flag (the actors are paid + slow ~20s), else it returns [].
    # NorthData is the keyed path to German Handelsregister data that bypasses
    # handelsregister.de's datacenter-IP block.
    apify_api_key: str | None = Field(
        default=None, description="Apify API token (read from APIFY_API_KEY)"
    )
    apify_enabled: bool = Field(
        default=False,
        description="Enable all Apify-backed providers (read from APIFY_ENABLED); needs apify_api_key",
    )

    # --- Gather / scrape tuning (speed) -------------------------------------
    provider_timeout: float = Field(
        default=6.0,
        description="Hard per-provider timeout (s) in the gather layer; an unresponsive "
        "source is dropped quickly so the row escalates instead of stalling. (Slow "
        "scrapers/Apify set their own higher search_timeout.)",
    )
    gather_deadline: float = Field(
        default=45.0,
        description="Hard overall cap (s) on the whole gather: after it, whatever sources "
        "responded are used and the rest are cancelled — the row never hangs. Set above the "
        "slowest source you actually want to wait for (NorthData ~35s); lower it for a "
        "snappier UI that escalates to web search sooner.",
    )
    handelsregister_scrape_fallback: bool = Field(
        default=True,
        description="Run the slow (~50s) handelsregister.de browser scrape. Turn off for "
        "fast batches — GLEIF + Impressum still cover DE",
    )
    enrichment_web_fill: bool = Field(
        default=True,
        description="Layer-2 web-search fill for missing Tier A/B fields (officers, etc.). "
        "On = more datapoints (scored); off = faster batches. The MCP branch's owner-lookup "
        "opt-in equivalent",
    )
    enrichment_web_fill_timeout: float = Field(
        default=20.0,
        description="Hard wall-clock cap (s) on the Layer-2 web-search fill. On timeout the "
        "row keeps what it has and missing fields stay blank — never a hang.",
    )
    # Precision-over-recall guard: the semantic matcher must NOT emit a 'match'
    # it rates below this. Below the floor it abstains (no_match -> blank) rather
    # than risk a wrong confident answer. Raise toward 0.7 for stricter precision.
    min_match_confidence: float = Field(
        default=0.5,
        description="Minimum semantic-match confidence to emit a match; below it the "
        "matcher abstains (blank) instead of guessing.",
    )
    # --- Persistent caching (testing / repeated lookups) --------------------
    search_cache_ttl_seconds: float = Field(
        default=7 * 24 * 3600.0,
        description="How long on-disk cached API search results stay valid. Long by default so "
        "re-running the same query skips the slow sources; clear via persistent_cache.clear()",
    )
    pipeline_result_cache: bool = Field(
        default=False,
        description="Cache the WHOLE pipeline answer per (name, jurisdiction) on disk and replay "
        "it with zero API/LLM calls. Off by default (so code changes take effect); turn on for "
        "fast repeat testing of unchanged logic",
    )

    # --- Pipeline (registry-lookup agent chain) ---
    # The Anthropic SDK reads ANTHROPIC_API_KEY from the environment itself.
    anthropic_model: str = Field(
        default="claude-opus-4-8", description="Model for the Layer-1 research agent"
    )
    matching_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Model for the matching-layer semantic filter — a constrained forced-tool "
        "pick-from-candidates task where Haiku matched Sonnet's accuracy at ~2x the speed "
        "(measured on the truth set), so it is the default for speed",
    )
    enrichment_model: str = Field(
        default="claude-sonnet-4-6",
        description="Model for the Layer-2 web-search enrichment fill. Kept on Sonnet: Haiku "
        "collapsed Tier A extraction (incorporation_date 0/50) in the measured run, so the "
        "enrichment model is split from the (faster) matcher model",
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
