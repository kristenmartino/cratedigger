"""Rebuild the taste profile for Kristen's user from data/kristen_seed.json.

Focused complement to seed.py:
  - seed.py creates user + sources + issue fixtures + taste profile —
    too much side-effect to safely re-run on every kristen_seed.json
    edit.
  - This script ONLY rebuilds the taste_profile row. No user creation,
    no source upserts, no issue fixtures.

Side-effect surface:
  - One SELECT on `users` to resolve user_id by clerk_id.
  - One UPSERT on `taste_profiles`.
  - One Voyage API call per artist in the seed list (build_profile_from_seed
    embeds them and means the centroid).

If clerk_id isn't found, exits non-zero — it means seed.py hasn't run
yet (no user row). Run that first, then this.

Requires:
  DATABASE_URL    — Neon connection string
  VOYAGE_API_KEY  — for embedding the seed artists

Usage:
  DATABASE_URL=... VOYAGE_API_KEY=... python scripts/rebuild_taste_profile.py

The repo's `.github/workflows/rebuild-taste-profile.yml` fires this on
every push to main that touches `kristen_seed.json` (path-trigger) and
also via workflow_dispatch for manual fires.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Make the agent package importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.db import close_pool, get_pool, init_pool  # noqa: E402
from agent.ingestion.seed_profile import (  # noqa: E402
    build_profile_from_seed,
    upsert_taste_profile,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("cratedigger-agent.rebuild_taste_profile")


# Same value as scripts/seed.py's KRISTEN_CLERK_ID. Single-user v1; when
# v1.1 adds multi-user, this script becomes per-user (probably via a
# clerk_id CLI arg).
KRISTEN_CLERK_ID = "user_kristen_seed_v1"


def find_seed_path() -> Path:
    """Walk up from this script to find data/kristen_seed.json."""
    start = Path(__file__).resolve()
    for parent in start.parents:
        candidate = parent / "services" / "agent" / "data" / "kristen_seed.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate services/agent/data/kristen_seed.json by "
        f"walking up from {start}."
    )


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return 1
    if not os.environ.get("VOYAGE_API_KEY"):
        logger.error("VOYAGE_API_KEY not set — build_profile_from_seed needs it")
        return 1

    try:
        seed_path = find_seed_path()
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    seed = json.loads(seed_path.read_text())
    n_artists = len(seed.get("artists", []))
    n_tags = len(seed.get("tags", []))
    logger.info(
        "Rebuilding profile from %s (%d artists, %d tags)",
        seed_path, n_artists, n_tags,
    )

    await init_pool()
    try:
        pool = await get_pool()

        user_id = await pool.fetchval(
            "SELECT id::text FROM users WHERE clerk_id = $1",
            KRISTEN_CLERK_ID,
        )
        if not user_id:
            logger.error(
                "No user with clerk_id=%r. Run scripts/seed.py first to "
                "create the user row, then re-run this.",
                KRISTEN_CLERK_ID,
            )
            return 1

        try:
            profile = await build_profile_from_seed(seed)
        except Exception as e:
            logger.error("build_profile_from_seed failed: %s", e)
            return 1

        try:
            await upsert_taste_profile(pool, user_id, profile)
        except Exception as e:
            logger.error("upsert_taste_profile failed: %s", e)
            return 1

        logger.info(
            "Rebuilt taste profile for %s (user_id=%s).",
            KRISTEN_CLERK_ID, user_id,
        )
    finally:
        await close_pool()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
