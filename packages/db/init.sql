-- Crate Digger — fresh-DB schema
-- Mirrors packages/db/src/schema.ts (Drizzle). When the Drizzle schema
-- changes, regenerate or hand-edit this file to match. The Drizzle definition
-- remains the source of truth; this file exists so docker-compose can
-- initialize a fresh local volume in one shot.
--
-- Postgres 16 + pgvector. RLS via current_setting('app.current_user_id', true)
-- on per-user tables.

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Enums ─────────────────────────────────────────────────────────────────

DO $$ BEGIN
  CREATE TYPE recommendation_category AS ENUM ('lead', 'steady', 'stretch', 'withheld');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE confidence_level AS ENUM ('high', 'medium', 'low');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE feedback_kind AS ENUM ('hit', 'miss', 'more_like_this');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE ingest_method AS ENUM ('rss', 'scrape', 'api');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE issue_status AS ENUM ('draft', 'published', 'delivered');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE agent_run_status AS ENUM ('running', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── users ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── taste_profiles ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS taste_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    seed            JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags            JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_weights  JSONB NOT NULL DEFAULT '{}'::jsonb,
    taste_centroid  VECTOR(1024),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE taste_profiles ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY taste_profiles_user_isolation ON taste_profiles
      USING (user_id::text = current_setting('app.current_user_id', true))
      WITH CHECK (user_id::text = current_setting('app.current_user_id', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── sources ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sources (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    slug              TEXT NOT NULL UNIQUE,
    ingest_method     INGEST_METHOD NOT NULL,
    ingest_url        TEXT NOT NULL,
    default_weight    NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    genre_affinity    TEXT[] NOT NULL DEFAULT '{}',
    last_crawled_at   TIMESTAMPTZ,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── releases ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS releases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    artist              TEXT NOT NULL,
    artist_normalized   TEXT NOT NULL,
    title_normalized    TEXT NOT NULL,
    label               TEXT,
    catalog_number      TEXT,
    release_date        DATE,
    url                 TEXT,
    bandcamp_url        TEXT,
    spotify_url         TEXT,
    cover_art_url       TEXT,
    embedding           VECTOR(1024),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    sources_seen        TEXT[] NOT NULL DEFAULT '{}',
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT releases_artist_title_unique UNIQUE (artist_normalized, title_normalized)
);

CREATE INDEX IF NOT EXISTS idx_releases_artist
    ON releases(artist_normalized);

CREATE INDEX IF NOT EXISTS idx_releases_label
    ON releases(label);

CREATE INDEX IF NOT EXISTS idx_releases_first_seen
    ON releases(first_seen_at);

-- pgvector ivfflat index needs rows to train on. Run after seed:
--   CREATE INDEX idx_releases_embedding ON releases
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- ── issues ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS issues (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    issue_number    INTEGER NOT NULL,
    volume          INTEGER NOT NULL DEFAULT 1,
    publish_date    DATE NOT NULL,
    status          ISSUE_STATUS NOT NULL DEFAULT 'draft',
    title           TEXT NOT NULL,
    editor_note     TEXT NOT NULL,
    sources_used    JSONB NOT NULL DEFAULT '{}'::jsonb,
    email_html      TEXT,
    agent_run_id    UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT issues_user_number_unique UNIQUE (user_id, issue_number)
);

CREATE INDEX IF NOT EXISTS idx_issues_user_publish
    ON issues(user_id, publish_date DESC);

-- ── recommendations ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS recommendations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id          UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    release_id        UUID NOT NULL REFERENCES releases(id) ON DELETE RESTRICT,
    position          INTEGER NOT NULL,
    category          RECOMMENDATION_CATEGORY NOT NULL,
    match_score       NUMERIC(4, 3) NOT NULL,
    confidence        CONFIDENCE_LEVEL NOT NULL,
    source_attr       TEXT NOT NULL,
    prose             TEXT NOT NULL,
    pull_quote        TEXT,
    matched_signals   JSONB NOT NULL DEFAULT '[]'::jsonb,
    cover_art_url     TEXT,
    withhold_until    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT recommendations_issue_position_unique UNIQUE (issue_id, position)
);

CREATE INDEX IF NOT EXISTS idx_recommendations_issue
    ON recommendations(issue_id);

CREATE INDEX IF NOT EXISTS idx_recommendations_release
    ON recommendations(release_id);

CREATE INDEX IF NOT EXISTS idx_recommendations_withhold
    ON recommendations(withhold_until)
    WHERE withhold_until IS NOT NULL;

-- ── feedback ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS feedback (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recommendation_id   UUID NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    kind                FEEDBACK_KIND NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT feedback_user_rec_unique UNIQUE (user_id, recommendation_id)
);

ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY feedback_user_isolation ON feedback
      USING (user_id::text = current_setting('app.current_user_id', true))
      WITH CHECK (user_id::text = current_setting('app.current_user_id', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_feedback_user
    ON feedback(user_id, created_at DESC);

-- ── annotations ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS annotations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recommendation_id   UUID NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    body                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE annotations ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY annotations_user_isolation ON annotations
      USING (user_id::text = current_setting('app.current_user_id', true))
      WITH CHECK (user_id::text = current_setting('app.current_user_id', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_annotations_user
    ON annotations(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_annotations_rec
    ON annotations(recommendation_id);

-- ── agent_runs ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id                UUID,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    status                  AGENT_RUN_STATUS NOT NULL DEFAULT 'running',
    current_step            TEXT,
    current_source          TEXT,
    sources_scanned         INTEGER NOT NULL DEFAULT 0,
    releases_scanned        INTEGER NOT NULL DEFAULT 0,
    candidates_considered   INTEGER NOT NULL DEFAULT 0,
    records_surfaced        INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status_started
    ON agent_runs(status, started_at DESC);

-- ── api_batches ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS api_batches (
    batch_id      TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'processing',
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_api_batches_status_kind
    ON api_batches(status, kind);
