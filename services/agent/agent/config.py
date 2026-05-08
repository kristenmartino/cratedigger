from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://cratedigger:cratedigger@localhost:5432/cratediggerdb"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    resend_api_key: str = ""
    resend_from_address: str = "editor@kristenmartino.ai"

    crawler_user_agent: str = "Crate Digger Music Digest / kristen@kristenmartino.ai"

    environment: str = "development"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
