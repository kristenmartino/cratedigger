"""Pydantic models. Mirrors the zod schemas in packages/shared/src/types.ts.

When you change a type there, update the matching model here. There's no
codegen — discipline-driven sync.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


# ── Enums ────────────────────────────────────────────────────────────────

RecommendationCategory = Literal["lead", "steady", "stretch", "withheld"]
ConfidenceLevel = Literal["high", "medium", "low"]
FeedbackKind = Literal["hit", "miss", "more_like_this"]
IngestMethod = Literal["rss", "scrape", "api"]
IssueStatus = Literal["draft", "published", "delivered"]
AgentRunStatus = Literal["running", "completed", "failed"]


# ── Health ───────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    db_connected: bool


# ── Matched signal (the "Why this matched you" tags) ────────────────────

class MatchedSignal(BaseModel):
    label: str
    weight: float | str | None = None
    variant: Literal["boosted", "stretch", "neutral"] = "neutral"


# ── Release ──────────────────────────────────────────────────────────────

class Release(BaseModel):
    id: UUID
    title: str
    artist: str
    label: str | None
    catalog_number: str | None = Field(alias="catalogNumber")
    release_date: date | None = Field(alias="releaseDate")
    url: HttpUrl | None
    bandcamp_url: HttpUrl | None = Field(alias="bandcampUrl")
    spotify_url: HttpUrl | None = Field(alias="spotifyUrl")
    cover_art_url: HttpUrl | None = Field(alias="coverArtUrl")
    sources_seen: list[str] = Field(alias="sourcesSeen")

    model_config = {"populate_by_name": True}


# ── Recommendation ───────────────────────────────────────────────────────

class Recommendation(BaseModel):
    id: UUID
    position: int = Field(ge=1, le=5)
    category: RecommendationCategory
    match_score: float = Field(ge=0.0, le=1.0, alias="matchScore")
    confidence: ConfidenceLevel
    source_attr: str = Field(alias="sourceAttr")
    prose: str
    pull_quote: str | None = Field(alias="pullQuote", default=None)
    matched_signals: list[MatchedSignal] = Field(alias="matchedSignals")
    cover_art_url: HttpUrl | None = Field(alias="coverArtUrl")
    withheld_until: datetime | None = Field(alias="withheldUntil", default=None)
    release: Release

    model_config = {"populate_by_name": True}


# ── Issue ────────────────────────────────────────────────────────────────

class Issue(BaseModel):
    id: UUID
    issue_number: int = Field(alias="issueNumber")
    volume: int
    publish_date: date = Field(alias="publishDate")
    status: IssueStatus
    title: str
    editor_note: str = Field(alias="editorNote")
    sources_used: dict[str, int] = Field(alias="sourcesUsed")
    recommendations: list[Recommendation]

    model_config = {"populate_by_name": True}


# ── Agent status ─────────────────────────────────────────────────────────

class AgentStatus(BaseModel):
    status: AgentRunStatus
    current_step: str | None = Field(alias="currentStep")
    current_source: str | None = Field(alias="currentSource")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(alias="completedAt")
    sources_scanned: int = Field(alias="sourcesScanned", default=0)
    releases_scanned: int = Field(alias="releasesScanned", default=0)
    candidates_considered: int = Field(alias="candidatesConsidered", default=0)
    records_surfaced: int = Field(alias="recordsSurfaced", default=0)

    model_config = {"populate_by_name": True}


# ── Feedback / Annotations input ─────────────────────────────────────────

class FeedbackInput(BaseModel):
    recommendation_id: UUID = Field(alias="recommendationId")
    kind: FeedbackKind

    model_config = {"populate_by_name": True}


class AnnotationInput(BaseModel):
    recommendation_id: UUID = Field(alias="recommendationId")
    body: str = Field(min_length=1, max_length=4000)

    model_config = {"populate_by_name": True}
