"""Tests for the categorization step (SPEC.md §4.2).

Pure-Python; no DB. Validates the 1-Lead / 2-Steady / 1-Stretch / 1-Withheld
selection rules and the promote-to-Stretch fallback.

All score fixtures are expressed as offsets from the threshold constants
so this file survives future calibration (no hardcoded magic numbers).
"""
from __future__ import annotations

from agent.categorization import (
    SCORE_LEAD_FLOOR,
    SCORE_STEADY_FLOOR,
    SCORE_STRETCH_FLOOR,
    ScoredCandidate,
    categorize,
)


# Score fixtures parameterized on thresholds. When thresholds shift,
# these still produce a candidate in each band — tests don't break.
_LEAD_VAL     = SCORE_LEAD_FLOOR + 0.10                              # firmly Lead
_STEADY_HIGH  = (SCORE_LEAD_FLOOR + SCORE_STEADY_FLOOR) / 2          # mid-Steady (upper)
_STEADY_LOW   = (SCORE_STEADY_FLOOR + _STEADY_HIGH) / 2              # mid-Steady (lower)
_STRETCH_VAL  = (SCORE_STEADY_FLOOR + SCORE_STRETCH_FLOOR) / 2       # mid-Stretch
_WITHHELD_VAL = max(0.0, SCORE_STRETCH_FLOOR - 0.02)                 # just below Stretch floor
_BELOW_ALL    = max(0.0, SCORE_STRETCH_FLOOR - 0.05)                 # below Stretch floor


def _candidate(
    release_id: str,
    score: float,
    artist: str = "Anon",
    sources: list[str] | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        release_id=release_id,
        artist=artist,
        score=score,
        # Tier-relative confidence so the fixture survives threshold changes.
        confidence=(
            "high" if score > SCORE_LEAD_FLOOR
            else "medium" if score > SCORE_STEADY_FLOOR
            else "low"
        ),
        components={},
        sources_seen=sources or ["boomkat"],
    )


SOURCE_WEIGHTS = {"boomkat": 1.0, "quietus": 0.9, "ra": 0.8}


def test_full_pool_yields_5_picks_in_correct_categories():
    cands = [
        _candidate("lead",     _LEAD_VAL,     artist="Loraine James"),
        _candidate("steady-a", _STEADY_HIGH,  artist="Yu Su"),
        _candidate("steady-b", _STEADY_LOW,   artist="upsammy"),
        _candidate("stretch",  _STRETCH_VAL,  artist="Klein"),
        _candidate("withheld", _WITHHELD_VAL, artist="Person"),
        _candidate("extra",    _BELOW_ALL,    artist="Other"),  # below stretch floor
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
    """Score must be > SCORE_LEAD_FLOOR, not just >=."""
    cands = [_candidate("borderline", SCORE_LEAD_FLOOR)]  # exactly the floor — not Lead
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)
    # Should fall into steady, not lead
    assert all(p.category != "lead" for p in picks)


def test_stretch_pool_empty_promotes_next_steady():
    """When no candidate sits in [Stretch_floor, Steady_floor), promote the
    next non-used to Stretch."""
    cands = [
        _candidate("lead",     _LEAD_VAL,     artist="A"),
        _candidate("steady-a", _STEADY_HIGH,  artist="B"),
        _candidate("steady-b", _STEADY_LOW,   artist="C"),
        # Nothing in [SCORE_STRETCH_FLOOR, SCORE_STEADY_FLOOR)
        _candidate("withheld", _WITHHELD_VAL, artist="D"),
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
    # Two candidates in the Stretch band, distinct scores so the order is
    # deterministic. Both above SCORE_STRETCH_FLOOR, below SCORE_STEADY_FLOOR.
    stretch_high = _STRETCH_VAL + 0.005
    stretch_low  = _STRETCH_VAL - 0.005
    cands = [
        _candidate("lead",         _LEAD_VAL,    artist="A"),
        _candidate("steady-a",     _STEADY_HIGH, artist="B"),
        _candidate("steady-b",     _STEADY_LOW,  artist="C"),
        _candidate("stretch-skip", stretch_high, artist="Klein"),    # fatigued
        _candidate("stretch-keep", stretch_low,  artist="upsammy"),
        _candidate("withheld",     _WITHHELD_VAL, artist="X"),
    ]
    picks = categorize(cands, recent_artists=["Klein"], source_weights=SOURCE_WEIGHTS)
    stretch_pick = next(p for p in picks if p.category == "stretch")
    assert stretch_pick.release_id == "stretch-keep"


def test_small_pool_returns_fewer_picks_gracefully():
    cands = [_candidate("only-one", _LEAD_VAL, artist="A")]
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)
    assert len(picks) == 1
    assert picks[0].category == "lead"


def test_source_attribution_picks_highest_weighted_source():
    """When a release is flagged by multiple sources, pick the heaviest-weighted
    one for the source_attr field (used by the editorial 'via X' tag)."""
    cands = [
        _candidate("multi", _LEAD_VAL, sources=["bandcamp-daily", "boomkat", "quietus"]),
    ]
    picks = categorize(cands, recent_artists=[], source_weights=SOURCE_WEIGHTS)
    assert picks[0].source_attr == "boomkat"  # highest weight in SOURCE_WEIGHTS


def test_thresholds_are_calibrated_to_observed_scoring():
    """The original SPEC numbers (0.85 / 0.65 / 0.50) were aspirational —
    the actual scoring formula tops out around 0.43-0.50 for a well-matched
    release, so those floors were never cleared.

    These calibrated thresholds (0.30 / 0.22 / 0.15) let scoring's typical
    output produce useful Lead/Steady/Stretch bands. They're a band-aid
    until scoring itself gets rescaled — when that happens, this test will
    be the trigger to bump them back up. See categorization.py header
    comment for the calibration rationale.
    """
    assert SCORE_LEAD_FLOOR == 0.30
    assert SCORE_STEADY_FLOOR == 0.22
    assert SCORE_STRETCH_FLOOR == 0.15
    # Sanity: floors are correctly ordered (Lead > Steady > Stretch > 0).
    assert SCORE_LEAD_FLOOR > SCORE_STEADY_FLOOR > SCORE_STRETCH_FLOOR > 0
