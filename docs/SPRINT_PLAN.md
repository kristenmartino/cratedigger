# Crate Digger — Sprint Plan

## Overview

**v1 ships in 6 weeks.** It's a single-user (Kristen) weekly digest with editorial reading surface, archive tool, weekly agent pipeline, email delivery, and feedback loop. By end of v1, the system has produced and delivered at least four real issues against real data.

**v1.1, v1.2, v1.3** are 2–3 week sprints stacked behind v1, each adding one significant feature.

The principle: ship a working narrow vertical first. Don't build for unknown future users until v1 is real.

## Decisions log (newest first)

**2026-05-08 — Option C (sequencing): personalization is v1-architectural; v1.1 ↔ v1.2 swap.**
Per-user taste profiles via quiz / playlist / history ingestion are not a v1.3 nice-to-have. v1 still ships single-user, but the seed script invokes the *real ingestion pipeline* (`agent.ingestion.seed_profile.build_profile_from_seed`) so the code path is exercised from day one. v1.1 (was annotations) is now multi-user onboarding; v1.2 is annotations; v1.3 is listening journal. See per-section notes below.

**2026-05-08 — Domain: subdomain, not standalone.**
Original handoff chose `cratedigger.ai` as an independent product domain. Reversed: shipping at `cratedigger.kristenmartino.ai` instead. Cheaper (no registration), faster (DNS already live for `kristenmartino.ai`), and the Resend domain-verification clock can run on the parent `kristenmartino.ai` (already required for the editor's email anyway). Matches Sift's pattern (`siftnews.kristenmartino.ai`). Re-evaluate at v1.3 multi-user — if the product opens up, a standalone domain may be worth the price for credibility.

**2026-05-08 — Option D (tech): Sift harvest, not fork.**
The "60% Sift reuse" estimate from the original handoff didn't survive the audit (real reuse: ~35% backend, ~25% frontend by LOC). Sift's news abstractions (10-category constant in 7 files, story-clusterer, civic-dossier tables, in-process FastAPI scheduler, two-repo split) actively work against CD's domain. Decision: **build per the handoff's monorepo + Drizzle + pnpm plan, harvest only specific leaf utilities and conventions from Sift.** See `../HARVESTED_FROM_SIFT.md` for the manifest.

---

## v1 — Six Weeks

### Week 1 — Foundation

**Goal:** A monorepo exists, the database schema is migrated, auth works, seed data populates, and a "hello world" of each app runs locally.

Deliverables:
- [ ] Monorepo initialized (pnpm workspaces) with the `apps/` and `services/` structure from CLAUDE.md
- [ ] Drizzle schema for all tables in SPEC.md §2 written and applied to a local Postgres
- [ ] Drizzle migration for production Neon DB applied
- [ ] FastAPI hello-world route returning JSON, deployable to Railway
- [ ] Next.js app rendering "Crate Digger" wordmark, deployable to Vercel
- [ ] Clerk wired to both apps, sign-in page reachable
- [ ] Seed script populates: 5 sources, Kristen's taste profile **via the real ingestion pipeline** (`agent.ingestion.seed_profile.build_profile_from_seed` — per Option C, no hardcoded `INSERT INTO taste_profiles`), 4 mock issues with full content from `mockups/cratedigger-newsletter.html`
- [ ] Sift harvest applied per Option D: leaf utilities (security headers, CSRF, rate-limit, sse, sanitize, utils, hooks), backend services (embedder w/ voyage-3 + 1024-dim swap, batch_client, batch_poller, usage_tracker), middleware, Dockerfile/railway/docker-compose. See `../HARVESTED_FROM_SIFT.md`.
- [ ] CLAUDE.md updated with actual run commands once verified

**Definition of done:** `pnpm dev` starts both apps, Kristen can sign in, the database has issue 04 in it (with her taste profile built through the real ingestion pipeline).

### Week 2 — Ingestion

**Goal:** The agent can read sources and produce a deduplicated, embedded list of releases. Not yet recommending — just reading.

Deliverables:
- [ ] RSS crawler implementation (feedparser-based) for The Quietus, Aquarium Drunkard, Bandcamp Daily
- [ ] Scrape crawler for Boomkat (handle their HTML structure carefully)
- [ ] Scrape crawler for Resident Advisor
- [ ] Normalization function: `(artist, title)` → canonical form for dedup
- [ ] Voyage AI embedding pipeline with retry/backoff
- [ ] `releases` table populated with one week of real data from all 5 sources
- [ ] Logging: each crawler run writes an `agent_runs` row with stats

**Definition of done:** running `python scripts/run_ingest.py` populates ~300 deduplicated releases for the week, each with an embedding. Can verify by SQL: `SELECT count(*) FROM releases WHERE first_seen_at > now() - interval '7 days'`.

### Week 3 — Scoring + categorization

**Goal:** Given the ingested releases and Kristen's taste profile, the agent picks 5 records and assigns categories.

Deliverables:
- [ ] Scoring function per SPEC.md §4.1 (α/β/γ/δ/ε weights)
- [ ] Categorization logic per SPEC.md §4.2
- [ ] LangGraph node skeleton: ingest → embed → score → categorize → persist (no prose yet)
- [ ] Output an `Issue` row with 5 `recommendations` rows, no prose populated yet
- [ ] Validation: pick a known-good test case (the actual Issue 04 from the mockup) and verify the agent picks the same Loraine James / Yu Su / upsammy / Klein records given a clean run

**Definition of done:** `python scripts/run_issue.py 5` (generate issue 5) produces a valid Issue with 5 recommendations, scored and categorized, no prose yet.

### Week 4 — Reasoning

**Goal:** The agent generates real editorial prose, pull quotes, matched-signals, and an editor's note that match the voice in the mockup.

Deliverables:
- [ ] Claude Haiku 4.5 prompt for prose generation. System prompt locks the voice (warm, specific, lowercase-confident). User prompt provides record data + matched signals + Kristen's taste profile context.
- [ ] Pull-quote generator (a separate single-shot call that picks the best 4–8 word phrase from the prose).
- [ ] Editor's note generator that frames the issue around the throughline (e.g., "the throughline this week is patience").
- [ ] Matched-signals tagger that produces the structured tag list shown in v4 mockup (boosted tags, sources, prior annotations, stretches).
- [ ] Validation: hand-review prose for issues 5 and 6 — does it sound like the mockup? Iterate the prompt if not.

**Definition of done:** running the full pipeline end-to-end produces an Issue with prose that's indistinguishable from the mockup's voice. Kristen reviews and approves.

### Week 5 — Frontend

**Goal:** Both surfaces (editorial digest + archive) render the database content matching the mockups pixel-for-pixel. Feedback wires through.

Deliverables:
- [ ] `/issue/[n]` route as RSC, matching `mockups/cratedigger-newsletter.html` (v4) — including system-strip, signal-blocks, matched-signals, sticky track-nav, now-digging widget
- [ ] `/archive` route, matching `mockups/cratedigger-archive.html` — including window chrome, sidebar filters, issue dividers, records grid, agent-status pulse
- [ ] Two-way handoff working: clicking "↗ Archive" on a record opens that record in the archive. Clicking "Open in editorial" in the archive returns to the digest.
- [ ] Feedback buttons (Hit / Miss / More like this) POST to `/api/feedback` and persist
- [ ] State badges on archive records reflect the user's actual feedback history
- [ ] Mobile responsive on the editorial; archive degrades gracefully

**Definition of done:** opening `cratedigger.kristenmartino.ai/issue/4` shows the same content as the v4 mockup, but pulled from the database. Clicking "Hit" on a record persists, refresh shows the badge in the archive.

### Week 6 — Email + Friday + polish

**Goal:** Email delivery works. Friday surprise drops separately. First real issue ships to Kristen's actual inbox.

Deliverables:
- [ ] MJML email template that renders the same Issue object as the web digest
- [ ] Resend integration with verified `kristenmartino.ai` domain (SPF/DKIM/DMARC configured)
- [ ] Saturday cron job: 9pm ET → run pipeline, generate issue, send email
- [ ] Friday cron job: 8am ET → fetch withheld record, send surprise email, mark as delivered
- [ ] Test send to Kristen + check rendering in Gmail, Apple Mail, Outlook
- [ ] First real issue (Issue 7 or whatever number is next) ships to Kristen on a real Sunday
- [ ] README updated with deploy notes, runbook for source-failure recovery

**Definition of done:** Kristen receives a real Crate Digger email Sunday morning. The web digest matches. The archive shows it. The withheld record arrives Friday. The agent-status widget on the web has accurate "now digging" state for the upcoming issue.

---

## v1.1 — Multi-user onboarding (2 weeks, after v1 ships)

**Goal:** Open Crate Digger to 1–2 invitees. The ingestion pipeline already exists from Week 1 (per Option C); this sprint exposes it through a real UI and a designed cold-start experience.

- [ ] Quiz onboarding flow: 5–10 questions → call `agent.ingestion.seed_profile.build_profile_from_seed` with `kind="quiz"` → write `taste_profiles` row
- [ ] Playlist-paste flow: text input or Spotify URL → `kind="playlist"` ingestion path (Spotify's playlist endpoint still works post-deprecation; only audio-features/recs are gone)
- [ ] Cold-start UX: a *designed* first-issue experience for fresh profiles. Generic-leaning until feedback shapes it. The signal-block UI honestly conveys low confidence on issue 01.
- [ ] Per-user issue generation in the weekly cron (was single-user-only in v1)
- [ ] Invite-code gating
- [ ] First invitees onboarded; first non-Kristen issues ship

This was originally v1.3. Brought forward per Option C — personalization is the load-bearing portfolio story; without it, v1 looks like a static newsletter for one person.

---

## v1.2 — Annotations (2 weeks)

**Goal:** Make the publication something users *write back to*. Annotations are notes on records that surface in the archive's "annotated" filter.

- [ ] Inline annotation UI on the editorial digest (highlight prose → add note)
- [ ] Annotation viewer in the archive (filter to "annotated only", browse personal notes)
- [ ] Annotations feed back into the matched-signals system (`"\"ambient with bones\" annotation · Mar"` becomes a real signal, not mockup-only)
- [ ] Markdown-supported, server-side sanitized

This was originally v1.1. Pushed back per Option C in favor of multi-user. Still high portfolio value: turns Crate Digger from "newsletter you read" into "publication you have a relationship with."

---

## v1.3 — Listening journal (3 weeks)

**Goal:** Cross-reference Crate Digger recommendations with what users actually listen to elsewhere. Also serves as the third ingestion path (`kind="history"`) for taste profiles.

- [ ] Last.fm OAuth integration (Last.fm still has a stable API)
- [ ] Daily sync of scrobbles into a `listens` table
- [ ] Archive view: "Listened" badge on records that show up in scrobbles
- [ ] Taste model update: heavy scrobbles strengthen related tags (same `taste_profiles.tags` weights the quiz/playlist paths populate)
- [ ] "Listening report" addendum to weekly issues — "you listened to last week's Loraine James recommendation 8 times"

This was originally v1.2. Pushed back per Option C. Closes the trust loop: the agent learns from passive behavior, not just explicit feedback.

---

## What's explicitly out of scope through v1.3

- Mobile apps (web + email is enough)
- Streaming integration (no audio playback in-product)
- Social features (no comments, sharing, public reactions)
- Discovery features (no "trending," no recommendations of recommendations)
- Stripe / payments (Crate Digger is free through v1.3, monetization is a separate decision)
- Multi-genre expansion (Crate Digger is opinionated about experimental electronic for v1; expanding is a v2 conversation)

## What's explicitly *in* scope through v1 that often gets cut

- The feedback loop (without it, the system is a static newsletter — non-negotiable)
- The two surfaces (without the archive, half the portfolio story is missing)
- The Friday surprise (without it, the cadence has dead time)
- Real source attribution (without it, the recommendations look generic-AI)
- The agent-status indicator (without it, the AI nature is invisible)

If anything has to be cut, cut the listening journal (now v1.3, per Option C). Don't cut from v1.

---

## Risks and dependencies

- **Source crawler fragility.** Boomkat scrape will break occasionally. Build retry + Slack alert + manual override to skip a bad source for one week.
- **Voyage AI / Claude API outages.** The pipeline must handle these gracefully — issue can be late, but should never silently fail to publish.
- **Email deliverability.** Get the domain verified Week 1, not Week 6. Discovering DNS issues 5 days before launch is the classic mistake.
- **Cold-start UX (v1.3).** Don't build v1.3 without a real plan for the first issue a new user receives. The current spec hand-waves this.
- **Over-scoping the agent.** The first version of the scoring function does not need to be sophisticated. It needs to *work* and *be observable*. Tune from feedback.
