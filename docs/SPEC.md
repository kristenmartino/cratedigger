# Crate Digger — Technical Spec

## 1. System overview

Two surfaces (editorial digest, archive) backed by a single agentic backend that runs a weekly LangGraph pipeline. Frontend is Next.js 15 RSC; backend is FastAPI on Railway; database is Postgres on Neon with pgvector.

```
┌─────────────────────────────────────────────────────────────┐
│  USER (Sunday morning, opens email or web)                   │
└─────────────────────────────────────────────────────────────┘
              │                          │
              ▼                          ▼
       ┌──────────┐             ┌────────────────┐
       │  Email   │             │  Web (Next.js) │
       │ (Resend) │             │  / /archive    │
       └──────────┘             └────────────────┘
              │                          │
              └──────────┬───────────────┘
                         ▼
                 ┌──────────────┐
                 │  FastAPI     │      ─── Read API: GET /issues/[n]
                 │  /api        │             GET /archive
                 │              │             POST /feedback
                 └──────────────┘
                         │
              ┌──────────┴───────────┐
              ▼                      ▼
       ┌──────────┐           ┌──────────────┐
       │ Postgres │           │  LangGraph   │   ─── Saturday cron
       │ + pgvec  │◄──────────│  pipeline    │       Friday cron (surprise)
       └──────────┘           └──────────────┘
              ▲                      │
              │                      ▼
       ┌──────────┐           ┌──────────────┐
       │  Voyage  │           │ Source       │
       │  AI      │           │ crawlers     │
       │ (embed)  │           │ (RSS/scrape) │
       └──────────┘           └──────────────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Claude       │
                              │ Haiku 4.5    │
                              │ (reasoning)  │
                              └──────────────┘
```

## 2. Data model

Postgres schema. Use Drizzle ORM (or Prisma if Sift uses it).

### users
Standard Clerk-mapped table.
```
id              uuid PK
clerk_id        text unique
email           text
created_at      timestamptz
```

### taste_profiles
One per user. Contains the user's seed and learned preferences.
```
id              uuid PK
user_id         uuid FK users.id
seed            jsonb        -- initial artists/genres/references from onboarding
tags            jsonb        -- {"hyperdub": 1.0, "dub-techno": 0.9, ...}
source_weights  jsonb        -- {"boomkat": 1.0, "quietus": 0.8, ...}
updated_at      timestamptz
```

### sources
The publications being monitored. Configurable.
```
id              uuid PK
name            text         -- "Boomkat", "The Quietus"
slug            text unique  -- "boomkat", "quietus"
ingest_method   text         -- "rss" | "scrape" | "api"
ingest_url      text
default_weight  numeric      -- 0.0–1.0
genre_affinity  text[]       -- tags this source is biased toward
last_crawled_at timestamptz
active          boolean
```

### releases
Every release the agent has scanned. Deduplicated.
```
id              uuid PK
title           text
artist          text
label           text
catalog_number  text
release_date    date
url             text         -- canonical URL (Bandcamp preferred)
embedding       vector(1024) -- Voyage AI
metadata        jsonb        -- raw extracted fields
first_seen_at   timestamptz
sources_seen    text[]       -- which source(s) flagged this
```

### issues
Weekly issues. One per user per week (or shared if v1 single-user).
```
id              uuid PK
user_id         uuid FK
issue_number    integer
volume          integer      -- "Vol. 1"
publish_date    date
status          text         -- "draft" | "published" | "delivered"
title           text         -- "A quieter week"
editor_note     text         -- the markdown editor's note
sources_used    jsonb        -- {"boomkat": 8, "quietus": 4, ...}
agent_run_id    uuid FK agent_runs.id
created_at      timestamptz
```

### recommendations
The records selected for an issue. 5 per issue (1 lead, 2 steady, 1 stretch, 1 withheld).
```
id              uuid PK
issue_id        uuid FK issues.id
release_id      uuid FK releases.id
position        integer       -- 1 to 5
category        text          -- "lead" | "steady" | "stretch" | "withheld"
match_score     numeric       -- 0.0–1.0
confidence      text          -- "high" | "medium" | "low"
source_attr     text          -- which source surfaced it (e.g., "boomkat")
prose           text          -- generated editorial paragraph (markdown)
pull_quote      text          -- only for lead
matched_signals jsonb         -- [{tag: "hyperdub", weight: 1.0, boosted: true}, ...]
cover_art_url   text          -- Bandcamp/label image, with fallback to SVG generator
withhold_until  timestamptz   -- for the Friday surprise
```

### feedback
User responses to recommendations. The training signal.
```
id              uuid PK
user_id         uuid FK
recommendation_id uuid FK
kind            text         -- "hit" | "miss" | "more_like_this"
created_at      timestamptz
```

### annotations
User notes on recommendations. Surfaced in archive's "annotated" filter.
```
id              uuid PK
user_id         uuid FK
recommendation_id uuid FK
body            text
created_at      timestamptz
```

### agent_runs
Every pipeline execution. Used for observability and the "now digging" widget.
```
id              uuid PK
issue_id        uuid FK   -- nullable; agent runs without producing issues too
started_at      timestamptz
completed_at    timestamptz
status          text         -- "running" | "completed" | "failed"
current_step    text         -- "ingesting" | "embedding" | "scoring" | etc.
current_source  text         -- "boomkat" — what the agent is currently reading
sources_scanned integer
releases_scanned integer
candidates_considered integer
records_surfaced integer
notes           text         -- error log or summary
```

The "now digging" / agent-status widgets in both UIs query the latest active row of `agent_runs` to display what the agent is doing right now.

## 3. Source ingestion

### 3.1 Configurable source layer

Sources are rows in the `sources` table, not hardcoded. v1 ships with five active sources but adding a sixth should be a database INSERT, not a code change.

v1 active sources:
- Boomkat (scrape)
- The Quietus (RSS)
- Aquarium Drunkard (RSS)
- Resident Advisor (scrape)
- Bandcamp Daily (RSS)

Future v1.x candidates: Tiny Mix Tapes, A Closer Listen, Ad Hoc, Pitchfork (experimental section), FACT Magazine.

### 3.2 Crawler implementation notes

- RSS-first. Use `feedparser` Python library.
- Scrape fallback for sources without feeds. Use `httpx` + `selectolax` (faster than BeautifulSoup). Respect `robots.txt`.
- Run crawlers in parallel (asyncio).
- Rate-limit per domain (max 1 req/sec).
- Always set a real `User-Agent`: `Crate Digger Music Digest / kristen@kristenmartino.ai`.
- Boomkat: scrape `boomkat.com/new-this-week` and follow product detail pages for descriptions. They've blocked aggressive scrapers in the past — be respectful or this will fail.
- Bandcamp Daily: RSS at `daily.bandcamp.com/feed`.

### 3.3 Deduplication

A "release" is identified by `(artist, title)` normalized (lowercased, ascii-folded, punctuation stripped). When the same release is mentioned in multiple sources within the same week, append to `sources_seen` rather than creating duplicates. This is what enables "11/11 sources flagged" type signals in the matched-signals UI.

## 4. Agent pipeline (LangGraph)

The weekly run is a graph with these nodes:

```
START
  │
  ▼
[ingest_sources] ──parallel──> [normalize_releases] ──> [embed_releases]
                                                              │
                                                              ▼
                                                       [score_for_user]
                                                              │
                                                              ▼
                                                       [categorize_picks]
                                                              │  (Lead, 2 Steady, Stretch, Withheld)
                                                              ▼
                                                       [generate_prose] ──parallel per record──> [generate_signals]
                                                              │
                                                              ▼
                                                       [generate_pull_quote]   (only for Lead)
                                                              │
                                                              ▼
                                                       [generate_editor_note]
                                                              │
                                                              ▼
                                                       [persist_issue]
                                                              │
                                                              ▼
                                                       [render_email]
                                                              │
                                                              ▼
                                                       [send_email]
                                                              │
                                                              ▼
                                                            END
```

### 4.1 Scoring algorithm

Match score for release R against user U:

```
score(R, U) =
    α * cosine_similarity(R.embedding, U.taste_centroid)
  + β * tag_overlap(R.tags, U.boosted_tags)
  + γ * source_authority(R.sources_seen, U.source_weights)
  + δ * recency(R.first_seen_at)
  - ε * fatigue(R.artist, U.recent_recommendations)
```

Initial weights: α=0.40, β=0.30, γ=0.20, δ=0.10, ε=0.15. Tune from feedback over time.

### 4.2 Categorization

After scoring all candidates:
- **Lead** = highest-scoring release with score > 0.85
- **Steady** = next 2 records with score in [0.65, 0.85]
- **Stretch** = highest-scoring record with score in [0.50, 0.65] AND not from a recently-recommended artist
- **Withheld** = next-highest-scoring release; held for Friday email

If the pool doesn't yield a stretch (no records in the 0.50-0.65 band that survive the artist-fatigue filter), promote the next Steady to Stretch and surface a different framing in the prose.

### 4.3 Prose generation

Use Claude Haiku 4.5 with a tight system prompt. The prompt should produce 2–3 sentences in the editorial voice established in the v4 mockup. Include the matched signals as context so the prose can reference them naturally ("you flagged Hyperdub-adjacent stuff in your seed back in February").

Important: the prose must NEVER hallucinate facts about the record. Catalog numbers, release dates, artist bios — all come from the structured release data, not the LLM. Pass them as context to the LLM and instruct it to use them verbatim.

### 4.4 Friday surprise job

Separate cron at Friday 8am ET. Updates the Withheld recommendation's `withhold_until` to NULL, regenerates the email shell to include just the surprise track, and sends to subscribers only.

## 5. API surface

FastAPI. All endpoints return JSON. Auth via Clerk JWT in `Authorization: Bearer <token>`.

### Public read

```
GET  /api/issues/:number              → Issue + recommendations (excluding withheld)
GET  /api/issues/:number/withheld     → Withheld record (auth required, subscribers only)
GET  /api/archive                     → list of all issues with summary stats
GET  /api/archive/issue/:id           → full issue detail for archive view
GET  /api/agent/status                → latest agent_run record (for "now digging" widgets)
```

### Authenticated write

```
POST /api/feedback                    → { recommendation_id, kind: "hit"|"miss"|"more_like_this" }
POST /api/annotations                 → { recommendation_id, body }
PATCH /api/taste-profile              → adjust source_weights or tags manually
```

### Internal (agent only)

```
POST /api/internal/issues             → persist a generated issue
PATCH /api/internal/agent-runs/:id    → update current step, current_source
```

## 6. Frontend routes

```
/                          → redirect to latest published issue
/issue/[number]            → editorial digest (matches cratedigger-newsletter.html mockup v4)
/archive                   → archive surface (matches cratedigger-archive.html mockup)
/archive/issue/[id]        → archive's expanded view of one issue
/onboarding                → taste profile setup (v1.3)
/settings                  → source weights, email frequency, account
```

The editorial route is RSC for SEO + performance. The archive can be a client component because it's interactive and behind auth.

## 7. Email rendering

MJML templates rendered server-side at issue-publish time. Stored as raw HTML in `issues.email_html` for replay/audit. Sent via Resend.

The email mirrors the web editorial structurally but simplifies:
- No sticky nav
- Halftone covers rendered as inline base64 PNGs (MJML does not support SVG well in Outlook)
- "Open in browser" link at top
- "Open in archive" link at bottom

Test deliverability against Litmus or Mailtrap before any production send.

## 8. Observability

- **Sentry** for backend errors
- **Vercel Analytics** for frontend (privacy-preserving mode)
- **Logs** structured to stdout, captured by Railway
- **Metrics dashboard:** small admin route at `/admin` (auth-gated to Kristen's email) showing: issues published, hit rate by category, agent run durations, source health

## 9. Costs (rough monthly estimate at v1 scale, single user)

- Vercel: $0 (hobby tier)
- Railway: $5 (FastAPI + cron)
- Neon: $0 (free tier sufficient at 1 user)
- Voyage AI: ~$1 (350 releases/week × 4 weeks × $0.0007/embedding)
- Claude Haiku 4.5: ~$3 (5 records × 4 issues × ~500 tokens × $0.001/1K)
- Resend: $0 (3K emails/mo free)
- Clerk: $0 (free tier)
- **Total: ~$10/month**

Scales linearly until ~1000 users. Plan for $50/mo at v1.3.

## 10. Security

- Clerk handles auth.
- API secrets in Railway environment variables, never committed.
- Rate limit feedback POSTs at 100/hour/user.
- Sanitize all user-submitted annotations on render (server-side markdown, allow-list only).
- HTTPS-only, HSTS enabled.
- No PII beyond email logged anywhere.
