from __future__ import annotations

import os

from pydantic_settings import BaseSettings

# Defensive cleanup: shells sometimes export sensitive keys as empty strings
# (e.g. `export ANTHROPIC_API_KEY=` in a profile to silence other tools).
# pydantic-settings prefers env vars over .env, so an empty env var would
# shadow a real .env value. Drop empties before settings are constructed.
for _key in (
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
    "RESEND_API_KEY",
    "DATABASE_URL",
):
    if os.environ.get(_key) == "":
        del os.environ[_key]


class Settings(BaseSettings):
    database_url: str = "postgresql://cratedigger:cratedigger@localhost:5432/cratediggerdb"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    resend_api_key: str = ""
    resend_from_address: str = "editor@kristenmartino.ai"

    # Polite-bot UA following the Googlebot/Feedly convention. The plain-text
    # "Crate Digger Music Digest" form was correctly identifying us, but generic
    # 403s from Cloudflare-fronted publishers (Quietus, Substacks, indie shops)
    # require the Mozilla/5.0 prefix to pass the default WAF rules. Mailto stays
    # so site owners can still reach us from server logs.
    crawler_user_agent: str = (
        "Mozilla/5.0 (compatible; CrateDigger/1.0; "
        "+mailto:kristen@kristenmartino.ai)"
    )

    # HMAC-checked on /v1/run-issue. Must match the value the cron caller sends.
    pipeline_api_key: str = "dev-key"

    environment: str = "development"
    log_level: str = "info"
    port: int = 8001

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
