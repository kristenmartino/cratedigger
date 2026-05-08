# Files harvested from Sift

Sift is a prior project — a news aggregator at `/Users/rootk/nextera-portfolio/news-aggregator/sift_v1/`. Crate Digger doesn't fork its repo; it adopts specific leaf utilities and conventions per the audit recommendation.

## Frontend (`apps/web/`)

| Crate Digger path | Source | Notes |
|---|---|---|
| `src/middleware.ts` | `sift/middleware.ts` | Clerk + fail-closed pattern. `PROTECTED_PREFIXES` swapped for CD's auth-required routes. |
| `src/lib/sanitize.ts` | `sift/lib/sanitize.ts` | `stripHtml` + `sanitizeUrl`. Verbatim. |
| `src/lib/security.ts` | `sift/lib/security.ts` | CSRF check (`Sec-Fetch-Site` first, then Origin/Referer). Verbatim. |
| `src/lib/sse.ts` | `sift/lib/sse.ts` | Server-Sent Events parser. Verbatim. |
| `src/lib/rate-limit.ts` | `sift/lib/rate-limit.ts` | In-memory sliding window. Verbatim. |
| `src/lib/utils.ts` | `sift/lib/utils.ts` | `timeAgo`, `extractSourceDomain`, `stableHash`. `formatUsdCompact` and `estimateReadTime` dropped. |

## Backend (`services/api/` + `services/agent/`)

| Crate Digger path | Source | Notes |
|---|---|---|
| `services/api/app/dependencies.py` | `sift-api/app/dependencies.py` | slowapi `Limiter`. Verbatim. |
| `services/api/app/db.py` (pool shell) | `sift-api/app/db.py` | asyncpg pool init/close + `_apply_migrations()` runner. Migration body rewritten for CD schema. |
| `services/api/app/main.py` | `sift-api/app/main.py` | `SecurityHeadersMiddleware`, CORS, lifespan, RateLimitExceeded handler. In-process scheduler removed; cron moved to Vercel/Railway scheduled jobs. |
| `services/agent/services/embedder.py` | `sift-api/services/embedder.py` | Voyage AI client. **Model swapped to `voyage-3`, dim swapped to 1024** per SPEC.md. |
| `services/agent/services/batch_client.py` | `sift-api/services/batch_client.py` | Anthropic Message Batches wrapper. Verbatim. |
| `services/agent/services/batch_poller.py` | `sift-api/services/batch_poller.py` | Batch poller skeleton. `HANDLERS` dict swapped for CD's prose/signals/pull-quote handlers. |
| `services/agent/services/usage_tracker.py` | `sift-api/services/usage_tracker.py` | Anthropic cost telemetry. Verbatim. |
| `services/agent/workflows/issue_workflow.py` | `sift-api/workflows/pipeline_workflow.py` | LangGraph `StateGraph(TypedDict)` skeleton. Nodes rewritten 1:1 for CD's pipeline. |
| `Dockerfile` (api & agent) | `sift-api/Dockerfile` | Verbatim. |
| `services/api/railway.toml` | `sift-api/railway.toml` | Verbatim. |
| `docker-compose.yml` | `sift-api/docker-compose.yml` | Verbatim, with CD database name + init.sql path. |

## Conventions adopted (no files — patterns)

- Numbered `migrations/NNN_*.sql` + paired `_apply_migrations()` in `app/db.py`. Operator-safe (CONCURRENTLY where applicable) and Railway-safe (idempotent IF NOT EXISTS).
- `init.sql` as fresh-DB source of truth; migrations are additive-only.
- RLS via `current_setting('app.current_user_id', true)` for per-user tables (`feedback`, `annotations`, `taste_profiles`).
- Defensive `try/catch` on missing tables/columns in DB queries — lets backend migrations land before frontend deploys.
- Pool `max: 5` for Neon (Neon connection limit).
- CSRF check via `Sec-Fetch-Site` first, then Origin/Referer fallback.
- HMAC `compare_digest` on internal pipeline-trigger headers.
- `X-Pipeline-Key` header + Vercel `CRON_SECRET` two-leg auth for cron triggers.
- `SecurityHeadersMiddleware` with CSP `default-src 'none'`, X-Frame-Options DENY, HSTS in production.
- JSON-array prompts with short keys (`i/s/c`) and JSON-recovery fallback when output is messy.
- Anthropic Message Batches for prose generation (50% discount; weekly cadence tolerates async).
- Voice-guard prompt header with explicit "never hallucinate catalog numbers / never editorialize tone / preserve attribution" rules.
- `log_usage()` cost telemetry on every Anthropic call.
- Skip-nav `<a href="#main-content">` + blocking theme-script in `<head>`.

## Conventions explicitly NOT adopted

- **Two-repo deploy.** Sift's own CLAUDE.md flags it as a recurring tripwire. CD uses the monorepo from the handoff plan.
- **In-process `asyncio` scheduler in FastAPI lifespan.** Sift uses it for 30-min cadence; CD uses Vercel cron / Railway scheduled jobs.
- **`stableHash(url + title)` djb2 32-bit primary key.** CD uses `gen_random_uuid()` + `UNIQUE(artist_normalized, title_normalized)`.
- **Hardcoded category constant.** Sift threads `top/technology/...` through 7 files; CD uses an enum on `recommendations.category` (`lead/steady/stretch/withheld`).
- **`from_search BOOLEAN` predicate.** News-specific; CD has no analog and would never need one.
- **`vector(512)` / `voyage-3-lite`.** SPEC.md specifies `vector(1024)` / `voyage-3` for CD.
