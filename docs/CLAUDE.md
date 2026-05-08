# CLAUDE.md — Crate Digger project context

This file is read by Claude Code at the start of every session. It is the persistent memory of the project. Update it when conventions change.

## What we're building

**Crate Digger** is a weekly AI-curated music recommendation digest. Every Sunday, an agent reads a configurable list of music criticism sources (Boomkat, The Quietus, Aquarium Drunkard, Resident Advisor, Bandcamp Daily, etc.), scores new releases against a personal taste model, and publishes an editorial digest of four records — plus a fifth held back for a Friday surprise email.

There are two surfaces:

1. **The editorial digest** (`/`, `/issue/[n]`, and the email itself) — the reading surface. Long-scroll, magazine-feel, slow. The user reads it Sunday morning with coffee.
2. **The archive** (`/archive`) — the tool surface. Window-chromed, system-aware, filterable. The user opens it on a Wednesday to flip through past issues, browse by source, see what the agent is currently doing.

Both surfaces render the same underlying content from the same database. They differ only in *mode*: one is for reading, one is for digging.

## What the agent does

A LangGraph pipeline runs every Saturday night to produce Sunday morning's issue:

1. **Ingest** — pull new releases from monitored sources (RSS, scrape fallback, dedup against past issues)
2. **Embed** — Voyage AI embeds release descriptions into pgvector
3. **Score** — for each user, compute a match score against their taste profile (boosted tags + seed history + source weights + recency)
4. **Categorize** — pick 1 Lead (highest confidence), 2 Steady (in-pocket matches), 1 Stretch (low-confidence but interesting), 1 Withheld (Friday drop)
5. **Reason** — Claude Haiku 4.5 generates the editorial paragraph, pull quote, and matched-signals tags for each record
6. **Render** — populate templates, generate email + web pages
7. **Deliver** — Resend for email, Vercel/static for web

A separate Friday job triggers the withheld-record email.

A continuous-running observer (`agent_status` table) lets the UI surface "what the agent is doing right now" — this is the pulsing-dot element in both surfaces.

## Tech stack

Same stack as Sift to maximize reuse:

- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind CSS
- **Backend:** FastAPI on Railway, Python 3.11+
- **Agent orchestration:** LangGraph
- **Database:** Postgres on Neon, with pgvector extension
- **Embeddings:** Voyage AI (voyage-3 or similar)
- **LLM:** Claude Haiku 4.5 for reasoning (`claude-haiku-4-5-20251001`), Sonnet for harder editorial tasks if needed
- **Auth:** Clerk
- **Email:** Resend with MJML templates
- **CI/CD:** GitHub Actions → Vercel (frontend) + Railway (backend)
- **Hosting:** `cratedigger.ai` (frontend), backend on a Railway subdomain

If you can fork from Sift's repo rather than start clean, do that. The auth, embedding, vector-search, and Claude-API plumbing already exist and are tested.

## Conventions

- **Always use TypeScript on the frontend.** No JS files unless absolutely necessary.
- **Type the API contract.** Use Pydantic models on the FastAPI side and generate matching TS types on the frontend.
- **No mock data in committed code.** Use seed scripts that populate a dev DB.
- **Server-side render everything that's public.** The editorial digest is SEO-relevant; render at request time or use ISR.
- **Email and web render from the same content.** The MJML email template and the Next.js page should pull from the same `Issue` API response. Don't write the editorial twice.
- **The agent is the source of truth for content.** Don't write copy in templates that the agent should be generating. If you find yourself hardcoding "via Boomkat" — stop, that's a database field.
- **Match the design system exactly.** The mockups define the visual language. See `DESIGN_SYSTEM.md` for tokens. Don't drift.
- **Mobile-first responsive on the editorial.** The archive is desktop-only-priority; the editorial must work on phones.
- **Keep the agent legible.** Every model decision should be inspectable. Save the matched-signals tags + scores per recommendation. Don't black-box.

## Code organization

```
/apps
  /web          ← Next.js 15 app (editorial + archive)
  /email        ← MJML templates + Resend integration
/services
  /agent        ← LangGraph pipeline (FastAPI wrapper)
  /api          ← FastAPI app for read/write endpoints
/packages
  /db           ← Drizzle or Prisma schema + migrations
  /shared       ← TS types shared between web and api
/scripts
  /seed         ← Seed sources, mock taste profile, sample issues
  /run-issue    ← Manually trigger an issue generation
```

## What NOT to do

- **Do not build a music-streaming feature.** Crate Digger does not stream. Listen links go to Bandcamp first, then Spotify if no Bandcamp page exists.
- **Do not call deprecated Spotify endpoints.** Audio features, audio analysis, recommendations, related-artists, and 30-second previews are all dead for new apps as of Nov 27, 2024. Use ListenBrainz / MusicBrainz / Last.fm for metadata if needed; otherwise rely on text signals only.
- **Do not generate album cover art with image models.** The mockups use SVG cover art that references actual label aesthetics. For real records, link to the label's actual cover art via Bandcamp / MusicBrainz / Discogs API. If unavailable, fall back to a generated SVG using the label's color palette.
- **Do not add ads, analytics tracking pixels, or third-party scripts.** This is a quiet publication.
- **Do not let the LLM hallucinate catalog numbers, label affiliations, or release dates.** Always pull these from the source's structured data or omit. A wrong catalog number breaks credibility instantly.
- **Do not skip the feedback loop.** Hit/Miss/More buttons must wire to the taste model from day one. Without the loop, the rest of the system is just a static newsletter.
- **Do not use `npm install` without checking `package.json` for the lockfile mode.** Use `pnpm` if a lockfile exists.

## How to run things (fill in as you build)

```bash
# Dev
pnpm dev                          # Next.js on :3000
uvicorn services.api.main:app --reload --port 8000

# DB
pnpm db:push                      # apply schema changes
pnpm db:seed                      # populate dev data

# Agent
python scripts/run_issue.py       # manually generate issue 04 for the dev user
```

## Where things live

- **Mockups:** `mockups/cratedigger-newsletter.html` (editorial), `mockups/cratedigger-archive.html` (archive). Open these in a browser to see the design intent. The HTML is implementation-ready — the same SVG cover art, type tokens, and component structure should map directly into React components.
- **Spec:** `SPEC.md` — data model, agent flows, API surface
- **Sprint plan:** `SPRINT_PLAN.md` — week-by-week build sequence
- **Design tokens:** `DESIGN_SYSTEM.md` — color, type, spacing, motion

## First-session prompts (copy one of these to start)

**For the very first session (foundation):**
> Read CLAUDE.md and SPEC.md. Then scaffold the monorepo per the structure in CLAUDE.md, set up Drizzle with the schema in SPEC.md, configure Clerk auth, and write a seed script that populates 4 issues of mock data using the content from `mockups/cratedigger-newsletter.html`. Don't build any UI yet. When done, show me what you scaffolded and we'll move to Sprint Week 1's deliverables.

**For frontend kickoff (after foundation exists):**
> Read CLAUDE.md, DESIGN_SYSTEM.md, and `mockups/cratedigger-newsletter.html`. Build the editorial digest page at `/issue/[n]` as a Next.js 15 RSC, matching the mockup pixel-for-pixel including the system-strip, signal blocks, matched-signals microsections, sticky track-nav, and now-digging widget. Pull data from the API; don't hardcode the content from the mockup. Show me the diff before applying.

**For agent kickoff (after data layer exists):**
> Read CLAUDE.md and SPEC.md sections 4-6. Build the LangGraph pipeline that ingests from the 5 sources in the seed data, embeds with Voyage, scores against my taste profile (use the seed profile from `scripts/seed`), and outputs a structured Issue object matching the schema. Don't render anything yet — just generate the data and write it to the DB. We'll wire the rendering separately.

**For the archive surface (later):**
> Read CLAUDE.md, DESIGN_SYSTEM.md, and `mockups/cratedigger-archive.html`. Build the `/archive` route as a desktop-priority interface matching the mockup, including the window chrome, sidebar filters, issue dividers, records grid with state badges, and live agent-status indicator. The agent-status should pull from the `agent_runs` table.

**For email rendering (Week 6):**
> Read CLAUDE.md and `mockups/cratedigger-newsletter.html`. Build the MJML email template that renders the same Issue object as the web digest, optimized for Gmail/Apple Mail/Outlook. Test renders for the same Issue 04 mock data should match the web version structurally; visual fidelity should be high but the email can simplify (e.g., no sticky nav, simpler riso effect on covers).

## Decisions made (don't relitigate)

- **Cadence:** Weekly Sunday + Friday surprise. Not on-demand.
- **Content type:** Text-only signals (no audio features — Spotify deprecated those). Editorial paragraphs are the primary value.
- **Cover art:** SVG-generated for mockups; for production records, fetch label-provided art via Bandcamp / Discogs APIs.
- **5 records per issue:** 1 Lead, 2 Steady, 1 Stretch, 1 Withheld. Locked.
- **Email is primary, web is secondary.** Email drives habit; web holds richer interactions.
- **Taste model:** Tag-based weighted scoring + recency + source authority. Not a deep model — interpretable wins over accurate at this scale.
- **Auth:** Clerk only. No password reset flows to build. SSO via Google.
- **Personalization is v1-architectural (Option C, 2026-05-08).** Per-user taste profiles populated via quiz / playlist paste / listening history are first-class — not a v1.3 nice-to-have. v1 still ships single-user (Kristen), but the seed script invokes the *real ingestion pipeline* (`agent.ingestion.seed_profile.build_profile_from_seed`) — no hardcoded `INSERT INTO taste_profiles`. v1.1 (originally annotations) becomes "expose that path behind a quiz UI + 1–2 invitees." Annotations push to v1.2; listening journal stays at v1.3.
- **Sift harvest, not fork (Option D, 2026-05-08).** The handoff's monorepo + Drizzle + pnpm plan stands. Sift contributes leaf utilities and conventions only — see `../HARVESTED_FROM_SIFT.md` for the manifest (auth wiring, embedder with model swap, batch_client, batch_poller, usage_tracker, RSS framework, security headers, CSRF, rate-limit, sse, sanitize, utils). Sift's two-repo deploy and in-process FastAPI scheduler are explicitly NOT adopted (Sift's own CLAUDE.md flags two-repo as a tripwire; CD's weekly cadence wants Vercel cron / Railway scheduled jobs, not lifespan-managed background tasks).

## Open questions

- **Source crawling:** RSS where available, scraping where not. Boomkat doesn't publish a clean feed — what's the fallback? (See SPEC.md §3.2.)
- **Email deliverability:** Verify domain SPF/DKIM/DMARC for `kristenmartino.ai` before scaling beyond Kristen's own inbox.
- **Cold-start UX:** When the first new user signs up, what does their first issue look like? They have no taste profile yet. Per Option C, the quiz onboarding lands in v1.1 — the experience needs to be designed before then. Even with the quiz, the first issue leans on the seed→tags pipeline; the model needs at least one feedback round to calibrate.
