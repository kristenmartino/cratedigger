"""Insert a row into the `users` table for a new invitee.

Tier 0 invitation flow:
  1. Invitee signs up via Clerk on cratedigger.kristenmartino.ai
  2. You grab their clerk_id from the Clerk dashboard
  3. Run this script to create their `users` row
  4. Commit data/seeds/<clerk_id>.json with their taste seed
  5. Rebuild workflow auto-fires → profile built → Sunday cron picks them up

This is the manual admin tool that the eventual Clerk webhook (Tier 1)
will replace. Until that lands, run this from local with $DATABASE_URL.

Idempotent: ON CONFLICT (clerk_id) DO UPDATE so re-runs reconcile email
changes rather than failing.

Usage:
  DATABASE_URL=... python scripts/add_user.py \\
    --clerk-id user_alice_2dRkF... \\
    --email alice@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Make the agent package importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.db import close_pool, get_pool, init_pool  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("cratedigger-agent.add_user")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clerk-id",
        required=True,
        help="Clerk's user id (e.g. user_2dRkF... — grab from Clerk dashboard)",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Invitee's email address (will receive the Sunday issue)",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL not set")
        return 1

    await init_pool()
    try:
        pool = await get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO users (clerk_id, email)
            VALUES ($1, $2)
            ON CONFLICT (clerk_id) DO UPDATE SET email = EXCLUDED.email
            RETURNING id::text, email, created_at
            """,
            args.clerk_id,
            args.email,
        )
    finally:
        await close_pool()

    logger.info(
        "User upserted: clerk_id=%s id=%s email=%s created_at=%s",
        args.clerk_id, row["id"], row["email"], row["created_at"].isoformat(),
    )
    print()
    print(f"  user_id:  {row['id']}")
    print(f"  clerk_id: {args.clerk_id}")
    print(f"  email:    {row['email']}")
    print()
    print("Next: commit services/agent/data/seeds/{clerk_id}.json with their seed,")
    print("then the rebuild-taste-profile workflow will fire on the path-trigger")
    print("and build their profile.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
