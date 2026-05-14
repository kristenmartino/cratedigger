"""Rebuild the taste profile for one user (or all users) from their seed file.

Per-user seed files live at `services/agent/data/seeds/<clerk_id>.json` —
filename-as-clerk_id keeps the routing implicit and lets git diffs scope
per-user (PR for Alice's taste update touches one file, not a shared one).

Modes:
  --clerk-id user_alice  → rebuild that user's profile only
  --all                  → enumerate every file in data/seeds/, rebuild each

Either way: build_profile_from_seed embeds the seed artists via Voyage,
then upsert_taste_profile updates the taste_profiles row in place. No
other side effects — no user creation, no sources sync, no issue fixtures.

If the seed file's named clerk_id doesn't have a row in `users`, the
script errors with a clear "run scripts/add_user.py first" message.

Requires:
  DATABASE_URL    — Neon connection string
  VOYAGE_API_KEY  — for embedding the seed artists

Usage:
  python scripts/rebuild_taste_profile.py --clerk-id user_kristen_seed_v1
  python scripts/rebuild_taste_profile.py --all

CI workflow .github/workflows/rebuild-taste-profile.yml fires this on
push-to-main changes to data/seeds/** (rebuilds --all) and on
workflow_dispatch with an explicit clerk_id input.
"""
from __future__ import annotations

import argparse
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


def find_seeds_dir() -> Path:
    """Walk up from this script to find services/agent/data/seeds/."""
    start = Path(__file__).resolve()
    for parent in start.parents:
        candidate = parent / "services" / "agent" / "data" / "seeds"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Could not locate services/agent/data/seeds/ by walking up from {start}."
    )


async def rebuild_one(pool, clerk_id: str, seed_path: Path) -> bool:
    """Rebuild a single user's profile. Returns True on success, False on
    a recoverable error (so --all can continue past per-user failures)."""
    if not seed_path.exists():
        logger.error("No seed file at %s (clerk_id=%s)", seed_path, clerk_id)
        return False

    seed = json.loads(seed_path.read_text())
    n_artists = len(seed.get("artists", []))
    n_tags = len(seed.get("tags", []))
    logger.info(
        "Rebuilding %s from %s (%d artists, %d tags)",
        clerk_id, seed_path.name, n_artists, n_tags,
    )

    user_id = await pool.fetchval(
        "SELECT id::text FROM users WHERE clerk_id = $1",
        clerk_id,
    )
    if not user_id:
        logger.error(
            "No user with clerk_id=%r. Run scripts/add_user.py first to "
            "create the user row, then re-run this.",
            clerk_id,
        )
        return False

    try:
        profile = await build_profile_from_seed(seed)
    except Exception as e:
        logger.error("build_profile_from_seed failed for %s: %s", clerk_id, e)
        return False

    try:
        await upsert_taste_profile(pool, user_id, profile)
    except Exception as e:
        logger.error("upsert_taste_profile failed for %s: %s", clerk_id, e)
        return False

    logger.info("Rebuilt taste profile for %s (user_id=%s).", clerk_id, user_id)
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--clerk-id",
        help="Clerk-mapped user id whose seed file (data/seeds/<id>.json) to rebuild",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Rebuild every seed file under data/seeds/",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL not set")
        return 1
    if not os.environ.get("VOYAGE_API_KEY"):
        logger.error("VOYAGE_API_KEY not set — build_profile_from_seed needs it")
        return 1

    try:
        seeds_dir = find_seeds_dir()
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    await init_pool()
    failures = 0
    try:
        pool = await get_pool()

        if args.clerk_id:
            seed_path = seeds_dir / f"{args.clerk_id}.json"
            ok = await rebuild_one(pool, args.clerk_id, seed_path)
            if not ok:
                failures += 1
        else:
            seed_paths = sorted(seeds_dir.glob("*.json"))
            if not seed_paths:
                logger.warning("No seed files found in %s — nothing to do.", seeds_dir)
                return 0
            logger.info("Rebuilding %d profiles from %s", len(seed_paths), seeds_dir)
            for path in seed_paths:
                clerk_id = path.stem  # filename without .json
                ok = await rebuild_one(pool, clerk_id, path)
                if not ok:
                    failures += 1
    finally:
        await close_pool()

    if failures:
        logger.error("%d profile(s) failed to rebuild", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
