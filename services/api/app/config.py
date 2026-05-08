from __future__ import annotations

from pydantic_settings import BaseSettings


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
    cors_origins: str = "http://localhost:3000,https://cratedigger.ai,https://www.cratedigger.ai"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
