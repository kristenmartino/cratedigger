"""Apply packages/db/init.sql to $DATABASE_URL.

The schema-change discipline we should have had from day one. init.sql
is idempotent (every CREATE TABLE / ADD COLUMN uses IF NOT EXISTS), so
this script is safe to run on every deploy — it's a no-op when the
schema matches.

Replaces the previous workflow of "Claude shipped an ALTER, paste it
manually into Neon, hope nobody forgets" — which is how we landed an
agent at one point that couldn't write to releases_dropped_as_news
because the column existed only in init.sql, not in Neon yet.

Usage:
  DATABASE_URL=... python scripts/migrate.py

CI workflow .github/workflows/migrate.yml runs this on every push to
main that touches packages/db/init.sql, plus workflow_dispatch for
manual fires.

Walks up the directory tree to find packages/db/init.sql so the script
runs from any working directory inside the monorepo.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import asyncpg


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("cratedigger-agent.migrate")


def find_init_sql() -> Path:
    """Walk up from this script until we find packages/db/init.sql.

    Raises FileNotFoundError with a helpful message if it isn't found —
    e.g. if someone runs this from a checkout that doesn't include the
    db package, the error tells them what's missing.
    """
    start = Path(__file__).resolve()
    for parent in start.parents:
        candidate = parent / "packages" / "db" / "init.sql"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate packages/db/init.sql by walking up from "
        f"{start}. The monorepo's packages/db package must be checked "
        "out alongside services/agent for migration to work."
    )


async def apply_migration(db_url: str, init_sql_path: Path) -> None:
    sql = init_sql_path.read_text()
    logger.info(
        "Applying %s (%d bytes) to database",
        init_sql_path, len(sql),
    )

    # Connect, execute, close. asyncpg.connect honors postgresql:// URLs
    # directly; no extra ssl/tls wrangling because Neon's URL embeds it.
    conn = await asyncpg.connect(db_url)
    try:
        # asyncpg.execute runs the whole SQL string as one batch. The file
        # uses `$$ ... $$` plpgsql blocks for the ENUM creation guard;
        # those parse cleanly in a single execute() call.
        await conn.execute(sql)
    finally:
        await conn.close()

    logger.info("Migration complete.")


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL is not set. Refusing to migrate.")
        return 1

    try:
        init_sql_path = find_init_sql()
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    try:
        await apply_migration(db_url, init_sql_path)
    except Exception as e:
        # asyncpg raises with the offending SQL fragment in the message —
        # surface it so a broken init.sql lands clearly in the workflow log.
        logger.error("Migration failed: %s", e)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
