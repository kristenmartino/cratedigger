"""Seed the local DB with sources, Kristen's taste profile, and 4 mock issues.

Per Option C (decision 2026-05-08): Kristen's taste profile is built via the
same ingestion pipeline a real onboarding flow will use — `build_profile_from_seed`
in `agent.ingestion.seed_profile`. NO hardcoded INSERT INTO taste_profiles.

Issue 04 is populated with the full content from
docs/mockups/cratedigger-newsletter.html (extracted to
data/issue_04_fixture.json). Issues 01-03 get placeholder titles only;
fill in later if needed for a fuller archive demo.

Usage:
    cd services/agent
    python scripts/seed.py [--reset]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Make the agent package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.db import close_pool, init_pool  # noqa: E402
from agent.ingestion.seed_profile import (  # noqa: E402
    build_profile_from_seed,
    upsert_taste_profile,
)

logger = logging.getLogger("cratedigger-agent.seed")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Kristen's Clerk-mapped identity for v1. Pre-seeded so the user row exists
# before Clerk-driven creation. When Clerk webhook lands, this can be reconciled.
KRISTEN_CLERK_ID = "user_kristen_seed_v1"
KRISTEN_EMAIL = "krissi889@gmail.com"


# ── Normalization (matches services/agent/agent/sources/rss.py) ──────────

_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    # Strip punctuation entirely (not replace with space) — otherwise
    # "Nous'klaer" and "Nousklaer" produce different keys and don't dedup.
    return _SPACE_RE.sub(" ", _PUNCT_RE.sub("", folded.lower())).strip()


# ── Steps ────────────────────────────────────────────────────────────────

async def reset_tables(pool) -> None:
    """Truncate all CD tables in dependency order. --reset only."""
    logger.warning("Resetting all tables")
    await pool.execute("""
        TRUNCATE TABLE
            feedback,
            annotations,
            recommendations,
            issues,
            agent_runs,
            api_batches,
            releases,
            taste_profiles,
            users,
            sources
        RESTART IDENTITY CASCADE
    """)


async def seed_sources(pool) -> None:
    sources = json.loads((DATA_DIR / "sources.json").read_text())
    for s in sources:
        await pool.execute(
            """
            INSERT INTO sources (name, slug, ingest_method, ingest_url,
                                 default_weight, genre_affinity, active)
            VALUES ($1, $2, $3::ingest_method, $4, $5, $6, $7)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                ingest_url = EXCLUDED.ingest_url,
                default_weight = EXCLUDED.default_weight,
                genre_affinity = EXCLUDED.genre_affinity,
                active = EXCLUDED.active
            """,
            s["name"], s["slug"], s["ingest_method"], s["ingest_url"],
            s["default_weight"], s["genre_affinity"], s["active"],
        )
    logger.info("Seeded %d sources", len(sources))


async def seed_user_and_profile(pool) -> str:
    """Create Kristen's user row and run her seed through the real ingestion path.
    Returns the user_id (UUID string)."""
    user_id = await pool.fetchval(
        """
        INSERT INTO users (clerk_id, email)
        VALUES ($1, $2)
        ON CONFLICT (clerk_id) DO UPDATE SET email = EXCLUDED.email
        RETURNING id::text
        """,
        KRISTEN_CLERK_ID, KRISTEN_EMAIL,
    )
    logger.info("Seeded user %s (%s)", KRISTEN_EMAIL, user_id)

    seed = json.loads((DATA_DIR / "kristen_seed.json").read_text())
    profile = await build_profile_from_seed(seed)
    await upsert_taste_profile(pool, user_id, profile)
    logger.info(
        "Seeded taste profile via real ingestion path "
        "(%d tags, %d sources, %d-dim centroid)",
        len(profile["tags"]),
        len(profile["source_weights"]),
        len(profile["taste_centroid"]) if profile["taste_centroid"] else 0,
    )
    return user_id


async def seed_issue_04(pool, user_id: str) -> None:
    """Seed Issue 04 with full mockup content. Creates the releases first,
    then the issue, then the 5 recommendations."""
    fixture = json.loads((DATA_DIR / "issue_04_fixture.json").read_text())

    # 1. Insert (or upsert) releases for non-withheld picks
    release_ids: dict[int, str] = {}
    for rec in fixture["recommendations"]:
        if rec["category"] == "withheld":
            continue
        rel = rec["release"]
        rid = await pool.fetchval(
            """
            INSERT INTO releases (
                title, artist, artist_normalized, title_normalized,
                label, catalog_number, release_date, url, bandcamp_url,
                sources_seen
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::date, $8, $9, $10)
            ON CONFLICT (artist_normalized, title_normalized) DO UPDATE SET
                label = EXCLUDED.label,
                catalog_number = EXCLUDED.catalog_number,
                release_date = EXCLUDED.release_date,
                url = EXCLUDED.url,
                bandcamp_url = EXCLUDED.bandcamp_url
            RETURNING id::text
            """,
            rel["title"],
            rel["artist"],
            normalize(rel["artist"]),
            normalize(rel["title"]),
            rel.get("label"),
            rel.get("catalog_number"),
            date.fromisoformat(rel["release_date"]) if rel.get("release_date") else None,
            rel.get("url"),
            rel.get("bandcamp_url"),
            [rec["source_attr"]],
        )
        release_ids[rec["position"]] = rid

    # 2. Insert the issue
    publish_date = date.fromisoformat(fixture["publish_date"])
    issue_id = await pool.fetchval(
        """
        INSERT INTO issues (
            user_id, issue_number, volume, publish_date, status, title,
            editor_note, sources_used
        )
        VALUES ($1::uuid, $2, $3, $4, 'published', $5, $6, $7::jsonb)
        ON CONFLICT (user_id, issue_number) DO UPDATE SET
            title = EXCLUDED.title,
            editor_note = EXCLUDED.editor_note,
            sources_used = EXCLUDED.sources_used
        RETURNING id::text
        """,
        user_id,
        fixture["issue_number"],
        fixture["volume"],
        publish_date,
        fixture["title"],
        fixture["editor_note"],
        json.dumps(fixture["sources_used"]),
    )

    # 3. Insert recommendations
    # For the withheld pick, we need a placeholder release row — keep its
    # title/artist as "(withheld)" so the schema's NOT NULL is satisfied.
    for rec in fixture["recommendations"]:
        if rec["position"] not in release_ids:
            rel = rec["release"]
            release_ids[rec["position"]] = await pool.fetchval(
                """
                INSERT INTO releases (
                    title, artist, artist_normalized, title_normalized
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (artist_normalized, title_normalized) DO UPDATE SET
                    title = EXCLUDED.title
                RETURNING id::text
                """,
                rel["title"],
                rel["artist"],
                normalize(rel["artist"]) or f"_withheld_{rec['position']}",
                normalize(rel["title"]) or f"_withheld_{rec['position']}",
            )

        await pool.execute(
            """
            INSERT INTO recommendations (
                issue_id, release_id, position, category, match_score,
                confidence, source_attr, prose, pull_quote, matched_signals,
                withhold_until
            )
            VALUES ($1::uuid, $2::uuid, $3, $4::recommendation_category, $5,
                    $6::confidence_level, $7, $8, $9, $10::jsonb, $11)
            ON CONFLICT (issue_id, position) DO UPDATE SET
                release_id = EXCLUDED.release_id,
                category = EXCLUDED.category,
                match_score = EXCLUDED.match_score,
                confidence = EXCLUDED.confidence,
                source_attr = EXCLUDED.source_attr,
                prose = EXCLUDED.prose,
                pull_quote = EXCLUDED.pull_quote,
                matched_signals = EXCLUDED.matched_signals,
                withhold_until = EXCLUDED.withhold_until
            """,
            issue_id,
            release_ids[rec["position"]],
            rec["position"],
            rec["category"],
            rec["match_score"],
            rec["confidence"],
            rec["source_attr"],
            rec["prose"],
            rec.get("pull_quote"),
            json.dumps(rec["matched_signals"]),
            datetime.fromisoformat(rec["withhold_until"].replace("Z", "+00:00"))
            if rec.get("withhold_until")
            else None,
        )

    logger.info("Seeded issue 04 with %d recommendations", len(fixture["recommendations"]))


async def seed_placeholder_issues(pool, user_id: str) -> None:
    """Issues 01-03: title + editor_note only, no recommendations. Real content
    can be backfilled by re-running the agent later."""
    placeholders = [
        (1, "2026-04-16", "A first dispatch", "An opening week. The agent has begun reading."),
        (2, "2026-04-23", "Listening sideways",
         "Three records that don't share a label or a year, but share a *patience*."),
        (3, "2026-04-30", "The dub revival",
         "Dub-techno is having another moment — and three records are leading."),
    ]
    for num, pubd, title, note in placeholders:
        await pool.execute(
            """
            INSERT INTO issues (user_id, issue_number, volume, publish_date,
                                status, title, editor_note, sources_used)
            VALUES ($1::uuid, $2, 1, $3, 'published', $4, $5, '{}'::jsonb)
            ON CONFLICT (user_id, issue_number) DO NOTHING
            """,
            user_id, num, date.fromisoformat(pubd), title, note,
        )
    logger.info("Seeded 3 placeholder issues (01–03)")


async def seed_agent_run(pool, issue_number: int = 5) -> None:
    """Insert a `running` agent_runs row so the now-digging widget has live state."""
    started = datetime.now(timezone.utc) - timedelta(minutes=2)
    await pool.execute(
        """
        INSERT INTO agent_runs (started_at, status, current_step, current_source,
                                sources_scanned, releases_scanned,
                                candidates_considered, records_surfaced,
                                notes)
        VALUES ($1, 'running', 'ingesting', 'boomkat', 11, 348, 47, 4, $2)
        """,
        started,
        f"reading sources for issue {issue_number}",
    )
    logger.info("Seeded a `running` agent_runs row for the now-digging widget")


# ── Main ─────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    pool = await init_pool()
    try:
        if args.reset:
            await reset_tables(pool)

        await seed_sources(pool)
        user_id = await seed_user_and_profile(pool)
        await seed_placeholder_issues(pool, user_id)
        await seed_issue_04(pool, user_id)
        await seed_agent_run(pool)

        logger.info("Seed complete.")
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="TRUNCATE all tables before seeding")
    asyncio.run(main(parser.parse_args()))
