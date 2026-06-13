import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The real `.env` lives at the repo root, but the app is usually launched from
# `backend/`. Resolve both by absolute path so the key is found regardless of
# CWD (in the Docker image neither exists and os.environ wins — that's fine).
_BACKEND_DIR = Path(__file__).resolve().parents[1]  # .../backend
_REPO_ROOT = Path(__file__).resolve().parents[2]  # repo root
_ENV_FILES = (str(_REPO_ROOT / ".env"), str(_BACKEND_DIR / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

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

    # --- gBizINFO (Japan, METI company information) ------------------------
    # Optional, keyed source for JP. Free token from https://info.gbiz.go.jp/api/;
    # if unset the provider disables itself and the keyless sources still work.
    gbizinfo_api_token: str | None = Field(
        default=None, description="gBizINFO API token (sent as x-hojinInfo-api-token)"
    )

    # --- Pipeline (registry-lookup agent chain) ---
    # The Anthropic SDK reads ANTHROPIC_API_KEY from os.environ. We also surface
    # it here so a key supplied via `.env` (pydantic) is honoured — see the
    # os.environ re-export below — and so the mock-gating in main.py can decide
    # whether real LLM/web-search calls are possible.
    anthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key for the matching (semantic-filter) LLM calls "
        "(env or .env). Without it, the chain falls back to deterministic stubs.",
    )
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


settings = Settings()

# The Anthropic SDK (and the mock-gating checks) read ANTHROPIC_API_KEY straight
# from os.environ. If the key was provided via `.env` (parsed by pydantic) but
# isn't exported in the process environment, re-export it here so a directly-run
# `uvicorn` — not just docker-compose's env_file — performs real LLM/web-search
# calls instead of silently falling back to mock.
if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
