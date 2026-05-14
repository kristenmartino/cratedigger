"""Tests for build_profile_from_seed's "manual" kind — the shape the
web onboarding form (apps/web/src/app/api/onboarding/route.ts) posts.

This is the contract test for Tier 1: when the form persists
{kind: "manual", artists: [...], tags: [...]} into taste_profiles.seed,
the agent's /v1/build-taste-profile endpoint reads it back and must be
able to derive a centroid + tag-weight map. If this test breaks, the
onboarding flow is broken.

Voyage's embed_query is mocked — we're testing the seed-shape transform,
not the embedding model.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.ingestion.seed_profile import (
    DEFAULT_SOURCE_WEIGHTS,
    _from_manual,
    build_profile_from_seed,
)


# ── _from_manual (pure transform) ───────────────────────────────────────


def test_manual_tags_get_full_weight():
    """Explicit tags from the form get weight 1.0 — they're the user's
    direct claim, not inferred signal. The scoring layer treats 1.0 as
    'always boost candidates carrying this tag'."""
    tags, _ = _from_manual(["Burial"], ["ambient", "dub"])
    assert tags == {"ambient": 1.0, "dub": 1.0}


def test_manual_centroid_text_lists_artists():
    """Artists are concatenated into a 'Music similar to:' prompt that
    becomes the embedding input. Order is preserved so re-runs of the
    same seed produce stable centroids."""
    _, centroid_text = _from_manual(
        ["Burial", "Four Tet", "Built to Spill"], []
    )
    assert centroid_text == "Music similar to: Burial, Four Tet, Built to Spill"


def test_manual_empty_artists_yields_empty_centroid():
    """No artists → no embedding input. embed_query would zero-vector
    this case, which is the right fail-soft."""
    _, centroid_text = _from_manual([], ["ambient"])
    assert centroid_text == ""


def test_manual_empty_tags_yields_empty_dict():
    """No tags is a valid form submission (we require >=3 in the UI,
    but the transform itself doesn't enforce minimums)."""
    tags, _ = _from_manual(["Burial"], [])
    assert tags == {}


# ── build_profile_from_seed end-to-end (with embed_query mocked) ────────


def test_build_profile_manual_kind_full_shape():
    """The end-to-end shape returned by build_profile_from_seed when the
    web form posts a manual seed. Matches the input to upsert_taste_profile
    and the columns in taste_profiles."""
    seed = {
        "version": 1,
        "kind": "manual",
        "artists": ["Burial", "Four Tet"],
        "tags": ["ambient", "dub"],
    }
    fake_centroid = [0.1] * 1024

    with patch(
        "agent.ingestion.seed_profile.embed_query",
        new=AsyncMock(return_value=fake_centroid),
    ):
        out = asyncio.run(build_profile_from_seed(seed))

    assert out["seed"] == seed
    assert out["tags"] == {"ambient": 1.0, "dub": 1.0}
    assert out["source_weights"] == DEFAULT_SOURCE_WEIGHTS
    assert out["taste_centroid"] == fake_centroid


def test_build_profile_manual_passes_full_artist_string_to_embed():
    """The embedding model gets the concatenated 'Music similar to: ...'
    string — this is the contract: if we changed the prefix, every
    existing user's centroid would shift, so the test pins the format."""
    seed = {
        "kind": "manual",
        "artists": ["Burial", "Four Tet"],
        "tags": [],
    }
    mock = AsyncMock(return_value=[0.0] * 1024)
    with patch("agent.ingestion.seed_profile.embed_query", new=mock):
        asyncio.run(build_profile_from_seed(seed))

    mock.assert_awaited_once_with("Music similar to: Burial, Four Tet")
