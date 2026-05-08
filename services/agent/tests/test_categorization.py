"""Tests for the categorization step (SPEC.md §4.2).

Pure-Python; no DB. Validates the 1-Lead / 2-Steady / 1-Stretch / 1-Withheld
selection rules and the promote-to-Stretch fallback.
"""
from __future__ import annotations

from agent.categorization import (
    SCORE_LEAD_FLOOR,
    SCORE_STEADY_FLOOR,
    SCORE_STRETCH_FLOOR,
    ScoredCandidate,
    categorize,
)


def _candidate(release_id: str, score: float, artist: str = "Anon", sources: list[str] | None = None) -> ScoredCandidate:
    return ScoredCandidate(
        release_id=release_id,
        artist=artist,
        score=score,
        confidence="high" if score > 0.8 else "medium" if score > 0.6 else "low",
        components={},
        sources_seen=sources or ["boomkat"],
    )


SOURCE_WEIGHTS = {"boomkat": 1.0, "quietus": 0.9, "ra": 0.8}


def test_full_pool_yields_5_picks_in_correct_categories():
    cands = [
        _candidate("lead", 0.92, artist="Loraine James"),
        _candidate("steady-a", 0.78, artist="Yu Su"),
        _candidate("steady-b", 0.74, artist="upsammy"),
        _candidate("stretch", 0.61, artist="Klein"),
        _candidate("withheld", 0.55, artist="Person"),
        _candidate("extra", 0.45, artist="Other"),  # below stretch floor
    ]
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)

    assert len(picks) == 5
    assert [p.position for p in picks] == [1, 2, 3, 4, 5]
    assert [p.category for p in picks] == ["lead", "steady", "steady", "stretch", "withheld"]
    assert picks[0].release_id == "lead"
    # Steady picks are the next-2 by score
    assert {p.release_id for p in picks if p.category == "steady"} == {"steady-a", "steady-b"}
    assert picks[3].release_id == "stretch"
    assert picks[4].release_id == "withheld"


def test_lead_floor_is_strict():
    """Score must be > 0.85, not just >=."""
    cands = [_candidate("borderline", SCORE_LEAD_FLOOR)]  # exactly 0.85 — not lead
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)
    # Should fall into steady, not lead
    assert all(p.category != "lead" for p in picks)


def test_stretch_pool_empty_promotes_next_steady():
    """When no candidate sits in [0.50, 0.65], promote the next non-used to Stretch."""
    cands = [
        _candidate("lead", 0.92, artist="A"),
        _candidate("steady-a", 0.78, artist="B"),
        _candidate("steady-b", 0.74, artist="C"),
        # Nothing in [0.50, 0.65]
        _candidate("withheld", 0.45, artist="D"),
    ]
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)

    stretch_pick = next((p for p in picks if p.category == "stretch"), None)
    assert stretch_pick is not None, "Stretch must be promoted from the next non-used"
    assert stretch_pick.release_id == "withheld"
    # And the original next-best (lower score) becomes withheld — but we only
    # had one lower candidate. The result depends on pool size; here the
    # stretch promotion consumes the only remaining candidate, so withheld is empty.
    assert len([p for p in picks if p.category == "withheld"]) == 0


def test_stretch_filters_out_recently_recommended_artist():
    """A candidate in the stretch band whose artist appears in recent_artists
    should be skipped in favor of the next artist."""
    cands = [
        _candidate("lead", 0.90, artist="A"),
        _candidate("steady-a", 0.80, artist="B"),
        _candidate("steady-b", 0.70, artist="C"),
        _candidate("stretch-skip", 0.62, artist="Klein"),  # fatigued
        _candidate("stretch-keep", 0.58, artist="upsammy"),
        _candidate("withheld", 0.52, artist="X"),
    ]
    picks = categorize(cands, recent_artists=["Klein"], source_weights=SOURCE_WEIGHTS)
    stretch_pick = next(p for p in picks if p.category == "stretch")
    assert stretch_pick.release_id == "stretch-keep"


def test_small_pool_returns_fewer_picks_gracefully():
    cands = [_candidate("only-one", 0.92, artist="A")]
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)
    assert len(picks) == 1
    assert picks[0].category == "lead"


def test_source_attribution_picks_highest_weighted_source():
    """When a release is flagged by multiple sources, pick the heaviest-weighted
    one for the source_attr field (used by the editorial 'via X' tag)."""
    cands = [
        _candidate("multi", 0.90, sources=["bandcamp-daily", "boomkat", "quietus"]),
    ]
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)
    assert picks[0].source_attr == "boomkat"  # highest weight in SOURCE_WEIGHTS


def test_thresholds_match_spec():
    """Sanity-check the constants match SPEC.md §4.2."""
    assert SCORE_LEAD_FLOOR == 0.85
    assert SCORE_STEADY_FLOOR == 0.65
    assert SCORE_STRETCH_FLOOR == 0.50
