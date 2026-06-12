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


settings = Settings()
