"""Categorization per SPEC.md §4.2.

After scoring all candidates:
  - Lead     = highest-scoring with score > 0.85
  - Steady   = next 2 with score in [0.65, 0.85]
  - Stretch  = highest-scoring in [0.50, 0.65] AND not from a recently-recommended artist
  - Withheld = next-highest-scoring; held for Friday email

If the pool yields no Stretch (no records in [0.50, 0.65] that survive
artist-fatigue), promote the next Steady to Stretch and surface a different
framing in the prose.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("cratedigger-agent.categorization")


@dataclass
class ScoredCandidate:
    release_id: str
    artist: str
    score: float
    confidence: str  # "high" | "medium" | "low"
    components: dict
    sources_seen: list[str]


@dataclass
class CategorizedPick:
    release_id: str
    position: int  # 1..5
    category: str  # "lead" | "steady" | "stretch" | "withheld"
    match_score: float
    confidence: str
    source_attr: str  # the highest-weighted source from sources_seen


SCORE_LEAD_FLOOR = 0.85
SCORE_STEADY_FLOOR = 0.65
SCORE_STRETCH_FLOOR = 0.50


def categorize(
    candidates: list[ScoredCandidate],
    *,
    recent_artists: list[str],
    source_weights: dict[str, float],
) -> list[CategorizedPick]:
    """Pick 5 records (1 Lead, 2 Steady, 1 Stretch, 1 Withheld) from scored candidates.

    Returns picks sorted by position. May return <5 if the pool is too small.
    """
    sorted_cands = sorted(candidates, key=lambda c: c.score, reverse=True)
    used: set[str] = set()
    picks: list[CategorizedPick] = []

    def primary_source(c: ScoredCandidate) -> str:
        if not c.sources_seen:
            return "unknown"
        return max(c.sources_seen, key=lambda s: source_weights.get(s, 0.0))

    # Lead
    lead = next((c for c in sorted_cands if c.score > SCORE_LEAD_FLOOR), None)
    if lead is not None:
        picks.append(CategorizedPick(
            release_id=lead.release_id, position=1, category="lead",
            match_score=lead.score, confidence=lead.confidence,
            source_attr=primary_source(lead),
        ))
        used.add(lead.release_id)

    # 2x Steady
    steady_pool = [
        c for c in sorted_cands
        if c.release_id not in used
        and SCORE_STEADY_FLOOR <= c.score <= SCORE_LEAD_FLOOR
    ]
    for i, c in enumerate(steady_pool[:2]):
        picks.append(CategorizedPick(
            release_id=c.release_id, position=2 + i, category="steady",
            match_score=c.score, confidence=c.confidence,
            source_attr=primary_source(c),
        ))
        used.add(c.release_id)

    # Stretch (with artist-fatigue filter)
    fatigue_set = {a.lower() for a in recent_artists}
    stretch_pool = [
        c for c in sorted_cands
        if c.release_id not in used
        and SCORE_STRETCH_FLOOR <= c.score < SCORE_STEADY_FLOOR
        and c.artist.lower() not in fatigue_set
    ]
    if stretch_pool:
        s = stretch_pool[0]
        picks.append(CategorizedPick(
            release_id=s.release_id, position=4, category="stretch",
            match_score=s.score, confidence=s.confidence,
            source_attr=primary_source(s),
        ))
        used.add(s.release_id)
    else:
        # Promote the next-best non-used to Stretch with reframed prose
        for c in sorted_cands:
            if c.release_id not in used:
                picks.append(CategorizedPick(
                    release_id=c.release_id, position=4, category="stretch",
                    match_score=c.score, confidence=c.confidence,
                    source_attr=primary_source(c),
                ))
                used.add(c.release_id)
                logger.info(
                    "Stretch pool empty; promoted candidate score=%.2f as Stretch",
                    c.score,
                )
                break

    # Withheld (next-highest non-used)
    for c in sorted_cands:
        if c.release_id not in used:
            picks.append(CategorizedPick(
                release_id=c.release_id, position=5, category="withheld",
                match_score=c.score, confidence=c.confidence,
                source_attr=primary_source(c),
            ))
            used.add(c.release_id)
            break

    picks.sort(key=lambda p: p.position)
    return picks
