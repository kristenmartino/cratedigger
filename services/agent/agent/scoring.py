"""Scoring algorithm per SPEC.md §4.1.

score(R, U) =
    α * cosine_similarity(R.embedding, U.taste_centroid)
  + β * tag_overlap(R.tags, U.boosted_tags)
  + γ * source_authority(R.sources_seen, U.source_weights)
  + δ * recency(R.first_seen_at)
  - ε * fatigue(R.artist, U.recent_recommendations)

Initial weights: α=0.40, β=0.30, γ=0.20, δ=0.10, ε=0.15. Tune from feedback.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger("cratedigger-agent.scoring")

# Default weights (initial). Tune over time from feedback rates.
ALPHA = 0.40  # taste cosine
BETA = 0.30   # tag overlap
GAMMA = 0.20  # source authority
DELTA = 0.10  # recency boost
EPSILON = 0.15  # artist-fatigue penalty

RECENCY_HALFLIFE_DAYS = 14


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity. Returns 0.0 if either vector is zero."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def tag_overlap(release_tags: list[str], boosted: dict[str, float]) -> float:
    """Sum of weights for tags the release shares with the user's boosted set,
    normalized by max possible (sum of all boosted weights)."""
    if not release_tags or not boosted:
        return 0.0
    matched = sum(boosted.get(t, 0.0) for t in release_tags)
    total = sum(boosted.values())
    return min(1.0, matched / total) if total > 0 else 0.0


def source_authority(sources_seen: list[str], source_weights: dict[str, float]) -> float:
    """Max weight among sources that flagged the release. (Highest-trust source wins.)"""
    if not sources_seen:
        return 0.0
    return max((source_weights.get(s, 0.0) for s in sources_seen), default=0.0)


def recency(first_seen_at: datetime | None) -> float:
    """Exponential decay with halflife = RECENCY_HALFLIFE_DAYS."""
    if first_seen_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    if first_seen_at.tzinfo is None:
        first_seen_at = first_seen_at.replace(tzinfo=timezone.utc)
    days = (now - first_seen_at).total_seconds() / 86400
    if days <= 0:
        return 1.0
    return math.exp(-days * math.log(2) / RECENCY_HALFLIFE_DAYS)


def artist_fatigue(artist: str, recent_artists: list[str]) -> float:
    """Penalize repeat artists. 1.0 if the artist appears in the last issue, decaying."""
    if not artist or not recent_artists:
        return 0.0
    artist_lower = artist.lower()
    # Most-recent artist gets full penalty; older ones decay
    for i, a in enumerate(recent_artists):
        if a.lower() == artist_lower:
            return 1.0 / (i + 1)
    return 0.0


def score_release(
    *,
    release_embedding: list[float],
    release_tags: list[str],
    release_sources: list[str],
    release_artist: str,
    release_first_seen_at: datetime | None,
    taste_centroid: list[float],
    boosted_tags: dict[str, float],
    source_weights: dict[str, float],
    recent_artists: list[str],
    weights: dict[str, float] | None = None,
) -> dict:
    """Compute the SPEC.md §4.1 score for a release against a user's taste.

    Returns {"score": float, "components": {...}} so the categorization
    layer can inspect which signal dominated.
    """
    w = weights or {
        "alpha": ALPHA, "beta": BETA, "gamma": GAMMA, "delta": DELTA, "epsilon": EPSILON,
    }

    cos = cosine_similarity(release_embedding, taste_centroid)
    tag = tag_overlap(release_tags, boosted_tags)
    src = source_authority(release_sources, source_weights)
    rec = recency(release_first_seen_at)
    fat = artist_fatigue(release_artist, recent_artists)

    raw = w["alpha"] * cos + w["beta"] * tag + w["gamma"] * src + w["delta"] * rec - w["epsilon"] * fat
    final = max(0.0, min(1.0, raw))

    return {
        "score": final,
        "components": {
            "cosine": cos,
            "tag_overlap": tag,
            "source_authority": src,
            "recency": rec,
            "artist_fatigue": fat,
        },
    }
