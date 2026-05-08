"""Tests for the scoring algorithm (SPEC.md §4.1).

Pure-Python; no DB, no LLM, no network. These run fast and are the safety
net for the math. Update when the weight constants change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.scoring import (
    artist_fatigue,
    cosine_similarity,
    recency,
    score_release,
    source_authority,
    tag_overlap,
)


# ── cosine_similarity ────────────────────────────────────────────────────

def test_cosine_identical_vectors():
    a = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, a) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_returns_zero():
    assert cosine_similarity([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 1.0, 1.0], [0.0, 0.0, 0.0]) == 0.0


def test_cosine_empty_or_mismatched_returns_zero():
    assert cosine_similarity([], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_clamped_to_unit_interval():
    # Floating-point edge: even with normalized vectors, the cosine can land
    # at 1.0000000002. Verify clamping.
    v = [0.5, 0.5, 0.5, 0.5]
    assert 0.0 <= cosine_similarity(v, v) <= 1.0


# ── tag_overlap ──────────────────────────────────────────────────────────

def test_tag_overlap_full_match():
    boosted = {"hyperdub": 1.0, "dub-techno": 0.9}
    assert tag_overlap(["hyperdub", "dub-techno"], boosted) == pytest.approx(1.0)


def test_tag_overlap_partial():
    boosted = {"hyperdub": 1.0, "dub-techno": 0.9, "ambient": 0.8}
    # Match 1.0 + 0.9 = 1.9 of total 2.7
    assert tag_overlap(["hyperdub", "dub-techno"], boosted) == pytest.approx(1.9 / 2.7)


def test_tag_overlap_no_match():
    boosted = {"hyperdub": 1.0}
    assert tag_overlap(["jazz", "country"], boosted) == 0.0


def test_tag_overlap_empty_inputs():
    assert tag_overlap([], {"x": 1.0}) == 0.0
    assert tag_overlap(["x"], {}) == 0.0


# ── source_authority ─────────────────────────────────────────────────────

def test_source_authority_picks_highest_weight():
    weights = {"boomkat": 1.0, "quietus": 0.9, "obscure-blog": 0.3}
    assert source_authority(["obscure-blog", "boomkat"], weights) == 1.0


def test_source_authority_missing_source_is_zero():
    weights = {"boomkat": 1.0}
    assert source_authority(["unknown-source"], weights) == 0.0


def test_source_authority_empty_list():
    assert source_authority([], {"boomkat": 1.0}) == 0.0


# ── recency ──────────────────────────────────────────────────────────────

def test_recency_now_is_one():
    now = datetime.now(timezone.utc)
    assert recency(now) == pytest.approx(1.0, abs=0.001)


def test_recency_decays_over_halflife():
    halflife = datetime.now(timezone.utc) - timedelta(days=14)
    assert recency(halflife) == pytest.approx(0.5, abs=0.05)


def test_recency_far_past_is_small():
    far_past = datetime.now(timezone.utc) - timedelta(days=180)
    assert recency(far_past) < 0.01


def test_recency_none_is_zero():
    assert recency(None) == 0.0


def test_recency_naive_datetime_handled():
    # Naive datetime should be treated as UTC, not crash
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert 0.95 <= recency(naive) <= 1.0


# ── artist_fatigue ───────────────────────────────────────────────────────

def test_artist_fatigue_most_recent_full_penalty():
    assert artist_fatigue("Loraine James", ["Loraine James", "Yu Su", "Klein"]) == 1.0


def test_artist_fatigue_decays_with_position():
    assert artist_fatigue("Klein", ["A", "B", "Klein"]) == pytest.approx(1 / 3)


def test_artist_fatigue_case_insensitive():
    assert artist_fatigue("LORAINE JAMES", ["loraine james"]) == 1.0


def test_artist_fatigue_no_match():
    assert artist_fatigue("upsammy", ["Loraine James", "Yu Su"]) == 0.0


def test_artist_fatigue_empty_inputs():
    assert artist_fatigue("", ["A", "B"]) == 0.0
    assert artist_fatigue("Loraine James", []) == 0.0


# ── score_release end-to-end ─────────────────────────────────────────────

def test_score_release_matches_strong_taste():
    """A release that matches every dimension should score near the ceiling."""
    centroid = [1.0, 0.0, 0.0, 0.0]
    result = score_release(
        release_embedding=[1.0, 0.0, 0.0, 0.0],  # cosine = 1.0
        release_tags=["hyperdub", "dub-techno"],
        release_sources=["boomkat"],
        release_artist="Brand New Artist",
        release_first_seen_at=datetime.now(timezone.utc),  # recency = 1.0
        taste_centroid=centroid,
        boosted_tags={"hyperdub": 1.0, "dub-techno": 1.0},
        source_weights={"boomkat": 1.0},
        recent_artists=[],
    )
    # alpha*1 + beta*1 + gamma*1 + delta*1 - epsilon*0 = 1.0
    assert result["score"] == pytest.approx(1.0, abs=0.01)
    assert result["components"]["cosine"] == pytest.approx(1.0)
    assert result["components"]["artist_fatigue"] == 0.0


def test_score_release_with_fatigue_penalty():
    """A repeat artist should get the score knocked down."""
    base_args = dict(
        release_embedding=[1.0, 0.0],
        release_tags=["hyperdub"],
        release_sources=["boomkat"],
        release_first_seen_at=datetime.now(timezone.utc),
        taste_centroid=[1.0, 0.0],
        boosted_tags={"hyperdub": 1.0},
        source_weights={"boomkat": 1.0},
    )
    fresh = score_release(release_artist="New Artist", recent_artists=[], **base_args)
    repeat = score_release(release_artist="Loraine James", recent_artists=["Loraine James"], **base_args)
    assert repeat["score"] < fresh["score"]


def test_score_release_clamped_to_unit_interval():
    """Pathological weights shouldn't push score outside [0, 1]."""
    result = score_release(
        release_embedding=[0.0],
        release_tags=[],
        release_sources=[],
        release_artist="X",
        release_first_seen_at=None,
        taste_centroid=[0.0],
        boosted_tags={},
        source_weights={},
        recent_artists=["X"],  # full fatigue, nothing else
    )
    assert 0.0 <= result["score"] <= 1.0
