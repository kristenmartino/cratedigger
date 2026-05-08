from __future__ import annotations

import os

from pydantic_settings import BaseSettings

# Defensive cleanup: shells sometimes export sensitive keys as empty strings
# (e.g. `export ANTHROPIC_API_KEY=` in a profile to silence other tools).
# Drop empties before settings construction so .env values aren't shadowed.
for _key in ("PIPELINE_API_KEY", "DATABASE_URL"):
    if os.environ.get(_key) == "":
        del os.environ[_key]


class Settings(BaseSettings):
    database_url: str = "postgresql://cratedigger:cratedigger@localhost:5432/cratediggerdb"
    pipeline_api_key: str = "dev-key"
    port: int = 8000
    environment: str = "development"
    log_level: str = "info"

    # Clerk JWT verification
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""

    # CORS allowlist (comma-separated)
    cors_origins: str = "http://localhost:3000,https://cratedigger.kristenmartino.ai"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
