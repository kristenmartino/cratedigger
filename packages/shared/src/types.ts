/**
 * Shared types between `apps/web` (TS) and `services/api` (Pydantic).
 *
 * The Drizzle schema in `@cratedigger/db` is the SQL source of truth.
 * This file defines the *runtime-validated* API contract — what the FastAPI
 * routes accept/return, and what the web app expects from them.
 *
 * Pydantic models in `services/api/app/models.py` mirror these shapes by hand
 * (no auto-codegen yet). Keep the two in sync.
 */
import { z } from "zod";

// ──────────────────────────────────────────────────────────────────────
// Enums (mirror packages/db/src/schema.ts)
// ──────────────────────────────────────────────────────────────────────

export const RecommendationCategory = z.enum([
  "lead",
  "steady",
  "stretch",
  "withheld",
]);
export type RecommendationCategory = z.infer<typeof RecommendationCategory>;

export const ConfidenceLevel = z.enum(["high", "medium", "low"]);
export type ConfidenceLevel = z.infer<typeof ConfidenceLevel>;

export const FeedbackKind = z.enum(["hit", "miss", "more_like_this"]);
export type FeedbackKind = z.infer<typeof FeedbackKind>;

export const IngestMethod = z.enum(["rss", "scrape", "api"]);
export type IngestMethod = z.infer<typeof IngestMethod>;

export const IssueStatus = z.enum(["draft", "published", "delivered"]);
export type IssueStatus = z.infer<typeof IssueStatus>;

export const AgentRunStatus = z.enum(["running", "completed", "failed"]);
export type AgentRunStatus = z.infer<typeof AgentRunStatus>;

// ──────────────────────────────────────────────────────────────────────
// Matched signals (the "Why this matched you" tag block in the editorial)
// ──────────────────────────────────────────────────────────────────────

export const MatchedSignal = z.object({
  /** Human-readable label, e.g. "Hyperdub seed", "Boomkat", "Caterina Barbieri seed" */
  label: z.string(),
  /** Numeric weight (0..1) OR a date stamp like "Mar" or "Feb" — UI handles both */
  weight: z.union([z.number(), z.string()]).nullable().optional(),
  /** "boosted" → teal pill; "stretch" → dashed coral; otherwise neutral */
  variant: z.enum(["boosted", "stretch", "neutral"]).default("neutral"),
});
export type MatchedSignal = z.infer<typeof MatchedSignal>;

// ──────────────────────────────────────────────────────────────────────
// Release (the underlying record)
// ──────────────────────────────────────────────────────────────────────

export const Release = z.object({
  id: z.string().uuid(),
  title: z.string(),
  artist: z.string(),
  label: z.string().nullable(),
  catalogNumber: z.string().nullable(),
  releaseDate: z.string().nullable(), // ISO date
  url: z.string().url().nullable(),
  bandcampUrl: z.string().url().nullable(),
  spotifyUrl: z.string().url().nullable(),
  coverArtUrl: z.string().url().nullable(),
  sourcesSeen: z.array(z.string()),
});
export type Release = z.infer<typeof Release>;

// ──────────────────────────────────────────────────────────────────────
// Recommendation (one record in an issue)
// ──────────────────────────────────────────────────────────────────────

export const Recommendation = z.object({
  id: z.string().uuid(),
  position: z.number().int().min(1).max(5),
  category: RecommendationCategory,
  matchScore: z.number().min(0).max(1),
  confidence: ConfidenceLevel,
  sourceAttr: z.string(), // "boomkat" | "quietus" | ...
  prose: z.string(), // markdown
  pullQuote: z.string().nullable(),
  matchedSignals: z.array(MatchedSignal),
  coverArtUrl: z.string().url().nullable(),
  withheldUntil: z.string().nullable(), // ISO datetime, only set for category="withheld"
  release: Release,
});
export type Recommendation = z.infer<typeof Recommendation>;

// ──────────────────────────────────────────────────────────────────────
// Issue (the weekly digest)
// ──────────────────────────────────────────────────────────────────────

export const Issue = z.object({
  id: z.string().uuid(),
  issueNumber: z.number().int().positive(),
  volume: z.number().int().positive(),
  publishDate: z.string(), // ISO date
  status: IssueStatus,
  title: z.string(), // "A quieter week"
  editorNote: z.string(), // markdown
  /** {"boomkat": 8, "quietus": 4, ...} */
  sourcesUsed: z.record(z.string(), z.number()),
  recommendations: z.array(Recommendation),
});
export type Issue = z.infer<typeof Issue>;

// ──────────────────────────────────────────────────────────────────────
// Agent status (the "now digging" widget + system-strip)
// ──────────────────────────────────────────────────────────────────────

export const AgentStatus = z.object({
  status: AgentRunStatus,
  currentStep: z.string().nullable(), // "ingesting" | "embedding" | ...
  currentSource: z.string().nullable(), // "boomkat"
  startedAt: z.string(), // ISO datetime
  completedAt: z.string().nullable(),
  sourcesScanned: z.number().int().min(0),
  releasesScanned: z.number().int().min(0),
  candidatesConsidered: z.number().int().min(0),
  recordsSurfaced: z.number().int().min(0),
});
export type AgentStatus = z.infer<typeof AgentStatus>;

// ──────────────────────────────────────────────────────────────────────
// Taste profile (per-user; populated via real ingestion, per Option C)
// ──────────────────────────────────────────────────────────────────────

export const TasteSeedKind = z.enum(["quiz", "playlist", "history", "manual"]);
export type TasteSeedKind = z.infer<typeof TasteSeedKind>;

export const TasteSeed = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("quiz"),
    answers: z.record(z.string(), z.union([z.string(), z.array(z.string())])),
  }),
  z.object({
    kind: z.literal("playlist"),
    /** Spotify/Apple Music URL or comma-separated artist list */
    source: z.enum(["spotify", "apple", "text"]),
    payload: z.string(),
  }),
  z.object({
    kind: z.literal("history"),
    source: z.enum(["lastfm", "spotify_export", "apple"]),
    payload: z.string(), // OAuth token, file URL, etc.
  }),
  z.object({
    kind: z.literal("manual"),
    artists: z.array(z.string()),
    tags: z.array(z.string()),
  }),
]);
export type TasteSeed = z.infer<typeof TasteSeed>;

export const TasteProfile = z.object({
  id: z.string().uuid(),
  userId: z.string().uuid(),
  seed: TasteSeed,
  /** {"hyperdub": 1.0, "dub-techno": 0.9, ...} */
  tags: z.record(z.string(), z.number()),
  /** {"boomkat": 1.0, "quietus": 0.8, ...} */
  sourceWeights: z.record(z.string(), z.number()),
  updatedAt: z.string(),
});
export type TasteProfile = z.infer<typeof TasteProfile>;

// ──────────────────────────────────────────────────────────────────────
// API request/response shapes
// ──────────────────────────────────────────────────────────────────────

export const FeedbackInput = z.object({
  recommendationId: z.string().uuid(),
  kind: FeedbackKind,
});
export type FeedbackInput = z.infer<typeof FeedbackInput>;

export const AnnotationInput = z.object({
  recommendationId: z.string().uuid(),
  body: z.string().min(1).max(4000),
});
export type AnnotationInput = z.infer<typeof AnnotationInput>;
