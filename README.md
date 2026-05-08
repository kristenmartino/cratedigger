# Crate Digger

A weekly AI-curated music recommendation digest. An agent reads music criticism sources every Saturday night, scores new releases against your taste profile, and publishes an editorial digest of four records Sunday morning — plus a fifth held back for a Friday surprise email.

Two surfaces, one agent:
- **Editorial digest** at `/issue/[n]` — the reading surface (long-scroll, magazine-feel)
- **Archive** at `/archive` — the tool surface (window-chromed, filterable)

Both render the same database content from the same `Issue` API response.

## Stack

- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind CSS + Drizzle ORM
- **Backend:** FastAPI on Railway (Python 3.12+)
- **Agent:** LangGraph weekly pipeline
- **Database:** Postgres on Neon, pgvector extension
- **Embeddings:** Voyage AI (voyage-3, 1024-dim)
- **LLM:** Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **Auth:** Clerk
- **Email:** Resend with MJML templates

## Layout

```
apps/
  web/         Next.js 15 — editorial + archive surfaces
  email/       MJML templates + Resend integration
services/
  api/         FastAPI read/write API
  agent/       LangGraph weekly pipeline
packages/
  db/          Drizzle schema + migrations + init.sql
  shared/      TS types shared between web and api
scripts/
  seed/        Seed sources, taste profile, sample issues
  run-issue/   Manually trigger an issue generation
```

## Run locally

```bash
# 1. Install
pnpm install

# 2. Start Postgres (pgvector image)
pnpm db:up

# 3. Apply schema (init.sql runs automatically on first volume create)
# To re-apply after edits:
pnpm db:reset

# 4. Seed sample issues
pnpm seed

# 5. Start the web app
pnpm dev:web         # → http://localhost:3000

# 6. (separate terminal) Start the API
pnpm api:dev         # → http://localhost:8000

# 7. (when wiring the agent) Run a manual issue
pnpm agent:run-issue
```

See [`docs/CLAUDE.md`](./docs/CLAUDE.md) for project context, [`SPEC.md`](./docs/SPEC.md) for architecture, [`SPRINT_PLAN.md`](./docs/SPRINT_PLAN.md) for the build plan.

## Conventions

- Shared types flow from `packages/shared` (zod) into `apps/web` and `services/api` (Pydantic mirrors them).
- DB schema source-of-truth: `packages/db/init.sql` for fresh DBs; `packages/db/migrations/NNN_*.sql` for additive changes.
- LLM editorial prose comes from `services/agent`. Never hardcode prose strings in templates.
- Real Bandcamp/Discogs cover art via the metadata fetcher; SVG-generated fallback for missing covers.

## What's harvested from Sift

This repo borrows leaf utilities and conventions from a prior project (Sift, a news aggregator). See [`HARVESTED_FROM_SIFT.md`](./HARVESTED_FROM_SIFT.md) for the manifest. The architecture is fresh per `docs/SPEC.md`; only specific files transferred.
