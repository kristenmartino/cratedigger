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

    # Real-browser UA. The polite-bot "Mozilla/5.0 (compatible; ...)" form was
    # still rejected by default Cloudflare WAF rules on Quietus, Boomkat,
    # Norman Records, Substack-hosted sites, etc. — the "(compatible;" token is
    # itself a block trigger on the rule set those sites use. We still respect
    # robots.txt and 1 req/sec; the UA is just the price of entry. Reachable
    # via the email in the site footer / repo if any operator wants to flag us.
    crawler_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Reddit "script app" OAuth credentials. Create an app of type "script" at
    # https://www.reddit.com/prefs/apps to get these. Without them, sources
    # with ingest_method="api" pointing at reddit.com will skip silently.
    reddit_client_id: str = ""
    reddit_client_secret: str = ""

    # HMAC-checked on /v1/run-issue. Must match the value the cron caller sends.
    pipeline_api_key: str = "dev-key"

    environment: str = "development"
    log_level: str = "info"
    port: int = 8001

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
