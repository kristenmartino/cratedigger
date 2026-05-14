"""Upsert services/agent/data/sources.json into the `sources` table.

Schema source of truth lives in packages/db/init.sql (applied by migrate.py).
SOURCE-CONFIG source of truth lives in services/agent/data/sources.json.
This script syncs the second to the database.

Why this exists:
  - sources.json is git-versioned, code-reviewable, easy to read in PRs.
  - The `sources` table is what the agent's pipeline actually reads at
    runtime (ingest_sources_node selects from it).
  - Without this sync, every sources.json change requires "remember to
    paste this INSERT/UPSERT into Neon by hand" — the same anti-pattern
    that migrate.py exists to kill.

Idempotent. ON CONFLICT (slug) DO UPDATE keeps the table in sync with
sources.json — including flipping `active` back and forth as sources are
enabled/disabled, and updating ingest_url / weight / genre_affinity on
in-place edits.

NOT handled here: deletions. If a source is removed from sources.json,
its row stays in the DB (with whatever state was last synced). To
deactivate, set active: false; to delete entirely, write the DELETE by
hand. Soft-deletion via the `active` flag is the supported workflow.

Usage:
  DATABASE_URL=... python services/agent/scripts/sync_sources.py

CI: run by .github/workflows/migrate.yml after the schema migration step.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import asyncpg


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("cratedigger-agent.sync_sources")


def find_sources_json() -> Path:
    """Walk up from this script until we find data/sources.json.

    Same pattern as migrate.py's find_init_sql so both scripts work from
    any working directory inside the monorepo.
    """
    start = Path(__file__).resolve()
    for parent in start.parents:
        candidate = parent / "services" / "agent" / "data" / "sources.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate services/agent/data/sources.json by walking up "
        f"from {start}. The monorepo's agent service must be checked out."
    )


async def sync(db_url: str, sources_path: Path) -> tuple[int, int]:
    """Upsert every row in sources.json. Returns (total_seen, total_active)."""
    sources = json.loads(sources_path.read_text())
    if not isinstance(sources, list):
        raise ValueError(f"{sources_path} must contain a JSON array")

    total = len(sources)
    active = sum(1 for s in sources if s.get("active"))

    conn = await asyncpg.connect(db_url)
    try:
        async with conn.transaction():
            for s in sources:
                await conn.execute(
                    """
                    INSERT INTO sources (
                        name, slug, ingest_method, ingest_url,
                        default_weight, genre_affinity, active
                    )
                    VALUES ($1, $2, $3::ingest_method, $4, $5, $6, $7)
                    ON CONFLICT (slug) DO UPDATE SET
                        name           = EXCLUDED.name,
                        ingest_method  = EXCLUDED.ingest_method,
                        ingest_url     = EXCLUDED.ingest_url,
                        default_weight = EXCLUDED.default_weight,
                        genre_affinity = EXCLUDED.genre_affinity,
                        active         = EXCLUDED.active
                    """,
                    s["name"],
                    s["slug"],
                    s["ingest_method"],
                    s["ingest_url"],
                    s["default_weight"],
                    s["genre_affinity"],
                    s["active"],
                )
    finally:
        await conn.close()

    return total, active


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL is not set. Refusing to sync.")
        return 1

    try:
        sources_path = find_sources_json()
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    try:
        total, active = await sync(db_url, sources_path)
    except Exception as e:
        logger.error("Source sync failed: %s", e)
        return 1

    logger.info(
        "Sync complete: %d sources upserted (%d active, %d inactive)",
        total, active, total - active,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
