"""Build a taste profile from a seed input.

This is the single entry point used by:
  - The seed script for Kristen's profile (v1, single-user)
  - The quiz onboarding flow (v1.1)
  - The playlist-paste flow (v1.1+)
  - The Last.fm history import (v1.2)

All four paths converge on the same `build_profile_from_seed()` function so
the schema and downstream consumers (scoring, the agent loop) don't branch
on input type.

Seed shape (matches packages/shared TasteSeed discriminated union):
  - {"kind": "quiz", "answers": {...}}
  - {"kind": "playlist", "source": "spotify"|"apple"|"text", "payload": "..."}
  - {"kind": "history", "source": "lastfm"|"spotify_export"|"apple", "payload": "..."}
  - {"kind": "manual", "artists": [...], "tags": [...]}

Output: taste_profiles row data — seed (jsonb), tags (jsonb weights),
source_weights (jsonb defaults), taste_centroid (vector(1024)).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agent.embedder import embed_query

logger = logging.getLogger("cratedigger-agent.ingestion.seed_profile")


# Default per-source weights. Tuned from feedback over time.
DEFAULT_SOURCE_WEIGHTS: dict[str, float] = {
    "boomkat": 1.0,
    "quietus": 0.9,
    "aquarium-drunkard": 0.7,
    "resident-advisor": 0.8,
    "bandcamp-daily": 0.7,
}


async def build_profile_from_seed(seed: dict[str, Any]) -> dict[str, Any]:
    """Convert a seed input into a taste_profiles row payload.

    Returns:
        {
            "seed":           the seed dict, stored as-is for replay/debug
            "tags":           {"hyperdub": 1.0, "dub-techno": 0.9, ...}
            "source_weights": {"boomkat": 1.0, ...}  (defaults; user can adjust)
            "taste_centroid": list[float] (1024-dim) — embedding of the seed text
        }
    """
    kind = seed.get("kind")
    if kind == "quiz":
        tags, centroid_text = _from_quiz(seed.get("answers", {}))
    elif kind == "playlist":
        tags, centroid_text = _from_playlist(seed.get("source", ""), seed.get("payload", ""))
    elif kind == "history":
        tags, centroid_text = _from_history(seed.get("source", ""), seed.get("payload", ""))
    elif kind == "manual":
        tags, centroid_text = _from_manual(seed.get("artists", []), seed.get("tags", []))
    else:
        raise ValueError(f"Unknown seed kind: {kind}")

    centroid = await embed_query(centroid_text)

    return {
        "seed": seed,
        "tags": tags,
        "source_weights": DEFAULT_SOURCE_WEIGHTS,
        "taste_centroid": centroid,
    }


# ── Per-kind handlers ────────────────────────────────────────────────────

def _from_manual(artists: list[str], explicit_tags: list[str]) -> tuple[dict[str, float], str]:
    """Manual seed: an artist list + optional tag list. Tags get weight 1.0
    (explicit signal); artists become the embedding centroid input."""
    tags = {tag: 1.0 for tag in explicit_tags}
    centroid_text = "Music similar to: " + ", ".join(artists) if artists else ""
    return tags, centroid_text


def _from_quiz(answers: dict[str, Any]) -> tuple[dict[str, float], str]:
    """Quiz answers → tag weights + centroid text.

    Stub for v1. Real implementation lands with the quiz UI in v1.1.
    Expected answers shape: {"genre_preference": ["ambient", "dub-techno"],
                             "vocal_tolerance": "instrumental",
                             "rhythm_density": "patient",
                             "seed_artists": ["Burial", "Tim Hecker"], ...}
    """
    tags: dict[str, float] = {}
    centroid_parts: list[str] = []

    if isinstance(answers.get("genre_preference"), list):
        for tag in answers["genre_preference"]:
            tags[tag] = tags.get(tag, 0) + 0.9

    if isinstance(answers.get("seed_artists"), list):
        centroid_parts.append("Music similar to: " + ", ".join(answers["seed_artists"]))

    return tags, " ".join(centroid_parts)


def _from_playlist(source: str, payload: str) -> tuple[dict[str, float], str]:
    """Playlist URL or pasted text → tag weights + centroid text.

    Stub for v1. Real implementation:
      - source="spotify" → fetch playlist tracks via Spotify Web API
        (the playlist endpoint still works post-deprecation; only audio
        features / recommendations are gone)
      - source="apple"   → MusicKit (requires Apple Developer)
      - source="text"    → just split the payload by line / comma
    """
    artists: list[str] = []
    if source == "text":
        artists = [a.strip() for a in payload.replace(",", "\n").split("\n") if a.strip()]
    # TODO: spotify, apple
    centroid_text = "Music similar to: " + ", ".join(artists) if artists else ""
    return {}, centroid_text


def _from_history(source: str, payload: str) -> tuple[dict[str, float], str]:
    """Listening history → tag weights + centroid text.

    Stub for v1. Real implementation:
      - source="lastfm" → OAuth, then fetch top-50 artists
      - source="spotify_export" → parse the user-initiated 'Your Data' export
      - source="apple"  → limited; defer to v1.2+
    """
    return {}, ""


# ── Convenience: write the row ───────────────────────────────────────────

async def upsert_taste_profile(pool, user_id: str, profile: dict[str, Any]) -> None:
    """Write the profile to taste_profiles. Idempotent on (user_id)."""
    centroid = profile.get("taste_centroid")
    centroid_str = "[" + ",".join(str(x) for x in centroid) + "]" if centroid else None

    await pool.execute(
        """
        INSERT INTO taste_profiles (user_id, seed, tags, source_weights, taste_centroid, updated_at)
        VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, $5::vector, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            seed = EXCLUDED.seed,
            tags = EXCLUDED.tags,
            source_weights = EXCLUDED.source_weights,
            taste_centroid = EXCLUDED.taste_centroid,
            updated_at = NOW()
        """,
        user_id,
        json.dumps(profile["seed"]),
        json.dumps(profile["tags"]),
        json.dumps(profile["source_weights"]),
        centroid_str,
    )
    logger.info("Upserted taste profile for user %s", user_id)
