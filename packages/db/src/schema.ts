/**
 * Crate Digger database schema.
 *
 * Source of truth for the schema. Mirrors `init.sql` (which exists for fresh-DB
 * Docker initialization). When you change this file:
 *   1. Run `pnpm db:generate` to emit a numbered migration file
 *   2. Update `init.sql` to match (or regenerate from the cumulative migration)
 *
 * Tables follow SPEC.md §2.
 *
 * Conventions:
 *  - UUID primary keys (`gen_random_uuid()`), not djb2 string hashes
 *  - `vector(1024)` for Voyage AI voyage-3 embeddings
 *  - Per-user tables (taste_profiles, feedback, annotations) use RLS via
 *    `current_setting('app.current_user_id', true)` — set in init.sql
 *  - JSONB for "structured but evolving" payloads (taste tags, matched signals)
 *  - timestamptz for all temporal columns
 */
import { sql } from "drizzle-orm";
import {
  boolean,
  date,
  index,
  integer,
  jsonb,
  numeric,
  pgEnum,
  pgTable,
  text,
  timestamp,
  unique,
  uuid,
  vector,
} from "drizzle-orm/pg-core";

// ──────────────────────────────────────────────────────────────────────
// Enums
// ──────────────────────────────────────────────────────────────────────

export const recommendationCategory = pgEnum("recommendation_category", [
  "lead",
  "steady",
  "stretch",
  "withheld",
]);

export const confidenceLevel = pgEnum("confidence_level", [
  "high",
  "medium",
  "low",
]);

export const feedbackKind = pgEnum("feedback_kind", [
  "hit",
  "miss",
  "more_like_this",
]);

export const ingestMethod = pgEnum("ingest_method", ["rss", "scrape", "api"]);

export const issueStatus = pgEnum("issue_status", [
  "draft",
  "published",
  "delivered",
]);

export const agentRunStatus = pgEnum("agent_run_status", [
  "running",
  "completed",
  "failed",
]);

// ──────────────────────────────────────────────────────────────────────
// users
//   Clerk-mapped. RLS-eligible via clerk_id.
// ──────────────────────────────────────────────────────────────────────

export const users = pgTable("users", {
  id: uuid("id").primaryKey().defaultRandom(),
  clerkId: text("clerk_id").notNull().unique(),
  email: text("email").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
});

// ──────────────────────────────────────────────────────────────────────
// taste_profiles
//   One per user. Holds seed input (quiz/playlist/history-derived) and
//   learned weights. Per Option C, this row is created via the real
//   ingestion pipeline (not hardcoded INSERTs in seed scripts).
// ──────────────────────────────────────────────────────────────────────

export const tasteProfiles = pgTable("taste_profiles", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" })
    .unique(),
  // Seed input from onboarding (quiz answers, playlist URL, scrobble import)
  // Shape: { kind: "quiz" | "playlist" | "history", payload: {...} }
  seed: jsonb("seed").notNull().default(sql`'{}'::jsonb`),
  // Learned tag weights, e.g. {"hyperdub": 1.0, "dub-techno": 0.9, ...}
  tags: jsonb("tags").notNull().default(sql`'{}'::jsonb`),
  // Per-source weights, e.g. {"boomkat": 1.0, "quietus": 0.8, ...}
  sourceWeights: jsonb("source_weights").notNull().default(sql`'{}'::jsonb`),
  // Centroid of the user's seed embeddings — used in cosine match in §4.1
  tasteCentroid: vector("taste_centroid", { dimensions: 1024 }),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
});

// ──────────────────────────────────────────────────────────────────────
// sources
//   Configurable. Adding a 6th source should be an INSERT, not a code change.
// ──────────────────────────────────────────────────────────────────────

export const sources = pgTable("sources", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: text("name").notNull(),
  slug: text("slug").notNull().unique(),
  ingestMethod: ingestMethod("ingest_method").notNull(),
  ingestUrl: text("ingest_url").notNull(),
  defaultWeight: numeric("default_weight", { precision: 3, scale: 2 })
    .notNull()
    .default("1.00"),
  genreAffinity: text("genre_affinity").array().notNull().default(sql`'{}'::text[]`),
  lastCrawledAt: timestamp("last_crawled_at", { withTimezone: true }),
  active: boolean("active").notNull().default(true),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
});

// ──────────────────────────────────────────────────────────────────────
// releases
//   Every release the agent has scanned. Deduplicated by normalized
//   (artist, title). Note: `vector(1024)` for voyage-3.
// ──────────────────────────────────────────────────────────────────────

export const releases = pgTable(
  "releases",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    title: text("title").notNull(),
    artist: text("artist").notNull(),
    // Normalized (lowercase, ascii-folded, punctuation-stripped) for dedup
    artistNormalized: text("artist_normalized").notNull(),
    titleNormalized: text("title_normalized").notNull(),
    label: text("label"),
    catalogNumber: text("catalog_number"),
    releaseDate: date("release_date"),
    url: text("url"), // canonical (Bandcamp preferred)
    bandcampUrl: text("bandcamp_url"),
    spotifyUrl: text("spotify_url"),
    appleMusicUrl: text("apple_music_url"),
    youtubeUrl: text("youtube_url"),
    soundcloudUrl: text("soundcloud_url"),
    coverArtUrl: text("cover_art_url"), // Bandcamp/Discogs/MB; SVG fallback handled in render layer
    embedding: vector("embedding", { dimensions: 1024 }),
    metadata: jsonb("metadata").notNull().default(sql`'{}'::jsonb`),
    sourcesSeen: text("sources_seen").array().notNull().default(sql`'{}'::text[]`),
    firstSeenAt: timestamp("first_seen_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => ({
    uniqArtistTitle: unique("releases_artist_title_unique").on(
      t.artistNormalized,
      t.titleNormalized,
    ),
    idxArtist: index("idx_releases_artist").on(t.artistNormalized),
    idxLabel: index("idx_releases_label").on(t.label),
    idxFirstSeen: index("idx_releases_first_seen").on(t.firstSeenAt),
    // pgvector ivfflat index for cosine similarity. Created after data exists
    // (ivfflat needs rows for training); see init.sql for the manual CREATE.
  }),
);

// ──────────────────────────────────────────────────────────────────────
// issues
//   Weekly issues. One per user per week (or shared in v1 single-user).
// ──────────────────────────────────────────────────────────────────────

export const issues = pgTable(
  "issues",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    issueNumber: integer("issue_number").notNull(),
    volume: integer("volume").notNull().default(1),
    publishDate: date("publish_date").notNull(),
    status: issueStatus("status").notNull().default("draft"),
    title: text("title").notNull(), // e.g. "A quieter week"
    editorNote: text("editor_note").notNull(), // markdown
    sourcesUsed: jsonb("sources_used").notNull().default(sql`'{}'::jsonb`),
    emailHtml: text("email_html"), // archived MJML render output
    agentRunId: uuid("agent_run_id"), // nullable; set on persist_issue
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => ({
    uniqUserIssueNumber: unique("issues_user_number_unique").on(
      t.userId,
      t.issueNumber,
    ),
    idxUserPublish: index("idx_issues_user_publish").on(
      t.userId,
      t.publishDate.desc(),
    ),
  }),
);

// ──────────────────────────────────────────────────────────────────────
// recommendations
//   5 per issue. Position 1 = lead, 2-3 = steady, 4 = stretch, 5 = withheld.
// ──────────────────────────────────────────────────────────────────────

export const recommendations = pgTable(
  "recommendations",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    issueId: uuid("issue_id")
      .notNull()
      .references(() => issues.id, { onDelete: "cascade" }),
    releaseId: uuid("release_id")
      .notNull()
      .references(() => releases.id, { onDelete: "restrict" }),
    position: integer("position").notNull(), // 1..5
    category: recommendationCategory("category").notNull(),
    matchScore: numeric("match_score", { precision: 4, scale: 3 }).notNull(),
    confidence: confidenceLevel("confidence").notNull(),
    sourceAttr: text("source_attr").notNull(), // e.g. "boomkat"
    prose: text("prose").notNull(), // editorial paragraph (markdown)
    pullQuote: text("pull_quote"), // only populated for lead
    matchedSignals: jsonb("matched_signals").notNull().default(sql`'[]'::jsonb`),
    coverArtUrl: text("cover_art_url"),
    withholdUntil: timestamp("withhold_until", { withTimezone: true }),
    withheldDeliveredAt: timestamp("withheld_delivered_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => ({
    uniqIssuePosition: unique("recommendations_issue_position_unique").on(
      t.issueId,
      t.position,
    ),
    idxIssue: index("idx_recommendations_issue").on(t.issueId),
    idxRelease: index("idx_recommendations_release").on(t.releaseId),
    idxWithhold: index("idx_recommendations_withhold")
      .on(t.withholdUntil)
      .where(sql`${t.withholdUntil} IS NOT NULL`),
  }),
);

// ──────────────────────────────────────────────────────────────────────
// feedback
//   The training signal. Hit/Miss/More-like-this.
//   RLS: users can only access their own.
// ──────────────────────────────────────────────────────────────────────

export const feedback = pgTable(
  "feedback",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    recommendationId: uuid("recommendation_id")
      .notNull()
      .references(() => recommendations.id, { onDelete: "cascade" }),
    kind: feedbackKind("kind").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => ({
    uniqUserRec: unique("feedback_user_rec_unique").on(
      t.userId,
      t.recommendationId,
    ),
    idxUser: index("idx_feedback_user").on(t.userId, t.createdAt.desc()),
  }),
);

// ──────────────────────────────────────────────────────────────────────
// annotations
//   User notes on recommendations. v1.2 feature; schema lands now.
// ──────────────────────────────────────────────────────────────────────

export const annotations = pgTable(
  "annotations",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    recommendationId: uuid("recommendation_id")
      .notNull()
      .references(() => recommendations.id, { onDelete: "cascade" }),
    body: text("body").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => ({
    idxUser: index("idx_annotations_user").on(t.userId, t.createdAt.desc()),
    idxRec: index("idx_annotations_rec").on(t.recommendationId),
  }),
);

// ──────────────────────────────────────────────────────────────────────
// agent_runs
//   Every pipeline execution. Powers the "now digging" widget.
// ──────────────────────────────────────────────────────────────────────

export const agentRuns = pgTable(
  "agent_runs",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    issueId: uuid("issue_id"), // nullable — agent runs without producing issues too
    startedAt: timestamp("started_at", { withTimezone: true }).defaultNow().notNull(),
    completedAt: timestamp("completed_at", { withTimezone: true }),
    status: agentRunStatus("status").notNull().default("running"),
    currentStep: text("current_step"), // "ingesting" | "embedding" | "scoring" | ...
    currentSource: text("current_source"), // "boomkat" — what the agent is reading
    sourcesScanned: integer("sources_scanned").notNull().default(0),
    releasesScanned: integer("releases_scanned").notNull().default(0),
    candidatesConsidered: integer("candidates_considered").notNull().default(0),
    recordsSurfaced: integer("records_surfaced").notNull().default(0),
    releasesDroppedAsNews: integer("releases_dropped_as_news").notNull().default(0),
    notes: text("notes"),
  },
  (t) => ({
    idxStatusStarted: index("idx_agent_runs_status_started").on(
      t.status,
      t.startedAt.desc(),
    ),
  }),
);

// ──────────────────────────────────────────────────────────────────────
// api_batches
//   Anthropic Message Batches tracking. Harvested pattern from Sift.
//   Rows persist until the poller marks them succeeded/errored/expired.
// ──────────────────────────────────────────────────────────────────────

export const apiBatches = pgTable(
  "api_batches",
  {
    batchId: text("batch_id").primaryKey(),
    kind: text("kind").notNull(), // 'prose' | 'signals' | 'pull_quote' | 'editor_note'
    status: text("status").notNull().default("processing"),
    submittedAt: timestamp("submitted_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    completedAt: timestamp("completed_at", { withTimezone: true }),
    metadata: jsonb("metadata").notNull().default(sql`'{}'::jsonb`),
  },
  (t) => ({
    idxStatusKind: index("idx_api_batches_status_kind").on(t.status, t.kind),
  }),
);

// ──────────────────────────────────────────────────────────────────────
// Type exports
// ──────────────────────────────────────────────────────────────────────

export type User = typeof users.$inferSelect;
export type NewUser = typeof users.$inferInsert;
export type TasteProfile = typeof tasteProfiles.$inferSelect;
export type NewTasteProfile = typeof tasteProfiles.$inferInsert;
export type Source = typeof sources.$inferSelect;
export type NewSource = typeof sources.$inferInsert;
export type Release = typeof releases.$inferSelect;
export type NewRelease = typeof releases.$inferInsert;
export type Issue = typeof issues.$inferSelect;
export type NewIssue = typeof issues.$inferInsert;
export type Recommendation = typeof recommendations.$inferSelect;
export type NewRecommendation = typeof recommendations.$inferInsert;
export type Feedback = typeof feedback.$inferSelect;
export type NewFeedback = typeof feedback.$inferInsert;
export type Annotation = typeof annotations.$inferSelect;
export type NewAnnotation = typeof annotations.$inferInsert;
export type AgentRun = typeof agentRuns.$inferSelect;
export type NewAgentRun = typeof agentRuns.$inferInsert;
export type ApiBatch = typeof apiBatches.$inferSelect;
export type NewApiBatch = typeof apiBatches.$inferInsert;
