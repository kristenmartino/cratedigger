"""FastAPI shell for the agent worker (Railway long-running service).

Two responsibilities:

1. **Batch recovery** — the pipeline generates prose live
   (`prose.generate_prose_live`), so `api_batches` is empty in practice.
   The poller is therefore armed only when a batch genuinely exists: once at
   startup, at submit time, or via POST /v1/internal/poll-batches — and it
   exits as soon as nothing is pending. It previously ran unconditionally
   every 60s against that empty table, which kept Neon's compute awake 24/7
   and was the project's largest database cost.

2. **HMAC-checked /v1/run-issue endpoint** — the trigger for the weekly
   LangGraph pipeline. Called by Vercel cron (Saturday 9pm ET) via the
   Next.js cron route handler, which forwards `X-Pipeline-Key`.

Read-only API endpoints (issues, archive, agent status, feedback) live in
services/api — that service stays read-only so its uptime isn't coupled
to agent failures.

Deployment: Railway service rooted at services/agent, builds the local
Dockerfile, runs `uvicorn agent.server:app`. Health check on /health.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from agent.batch_client import count_pending_batches
from agent.batch_poller import ensure_poller_running, poller_is_running, sweep_once
from agent.config import settings
from agent.db import close_pool, get_pool
from agent.friday_drop import deliver_friday_drops_for_user
from agent.ingestion.seed_profile import (
    build_profile_from_seed,
    upsert_taste_profile,
)
from agent.sources.spotify_playlist import (
    fetch_playlist_artists,
    parse_playlist_url,
)
from agent.spotify_sync import sync_for_all_connected_users, sync_playlist_for_user
from agent.workflows.issue_workflow import IssueState, issue_pipeline

logger = logging.getLogger("cratedigger-agent.server")

API_VERSION = "0.1.0"


def _database_host() -> str:
    """Host portion of DATABASE_URL, for logging. Never returns credentials."""
    try:
        return urlsplit(settings.database_url).hostname or "unknown"
    except Exception:
        return "unparseable"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.info("Starting cratedigger-agent (env=%s)", settings.environment)

    if settings.pipeline_api_key in ("dev-key", "change-me-in-production", ""):
        logger.warning(
            "SECURITY: PIPELINE_API_KEY is set to a default/empty value. "
            "Set a strong, unique key matching the value used by the cron caller."
        )

    # No pool init here on purpose — connections opened at startup are held
    # for the life of the process, which stops Neon from ever suspending its
    # compute. The pool is created lazily on first query (agent/db.py).
    logger.info("Database configured (host=%s)", _database_host())

    # Startup re-arm for the batch poller, which no longer runs on a timer.
    # Its state is in-process, so a redeploy mid-batch would otherwise orphan
    # one. Exactly ONE query at boot decides whether to arm — not one per
    # minute, which is what this replaced. The 48h bound stops an ancient
    # stuck row from re-arming the poller on every boot forever.
    try:
        pending = await count_pending_batches(within_seconds=48 * 60 * 60)
        if pending:
            logger.info("Found %d pending batch(es) at startup — arming poller", pending)
            ensure_poller_running()
        else:
            logger.info("No pending batches at startup — poller idle")
    except Exception as e:
        # A Neon hiccup at boot must not crash the service. POST
        # /v1/internal/poll-batches (or the next submit) re-arms.
        logger.warning("Startup batch check failed: %s", e)

    yield

    await close_pool()
    logger.info("cratedigger-agent shut down")


app = FastAPI(
    title="Crate Digger Agent",
    version=API_VERSION,
    description=(
        "LangGraph weekly pipeline trigger + Anthropic Message Batches poller. "
        "Read API lives separately at services/api."
    ),
    lifespan=lifespan,
)


# ── Security headers (harvested verbatim from services/api) ──────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ── Auth ─────────────────────────────────────────────────────────────────

def _verify_pipeline_key(x_pipeline_key: str | None) -> None:
    """Constant-time check on the X-Pipeline-Key header.
    Used to gate the /v1/run-issue trigger endpoint.
    """
    if not x_pipeline_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Pipeline-Key",
        )
    if not hmac.compare_digest(x_pipeline_key, settings.pipeline_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Pipeline-Key",
        )


# ── Routes ───────────────────────────────────────────────────────────────

@app.get("/", summary="Service info")
async def root():
    return {
        "service": "cratedigger-agent",
        "version": API_VERSION,
        "endpoints": {
            "health": "GET /health",
            "health_db": "GET /health/db",
            "run_issue": "POST /v1/run-issue",
            "poll_batches": "POST /v1/internal/poll-batches",
            "friday_drop": "POST /v1/friday-drop",
            "build_taste_profile": "POST /v1/build-taste-profile",
            "parse_playlist": "POST /v1/parse-playlist",
            "sync_spotify_playlist": "POST /v1/sync-spotify-playlist",
        },
    }


@app.get("/health", summary="Liveness check")
async def health():
    """Liveness only — deliberately does NOT touch Postgres.

    This is Railway's deploy gate. Querying the DB here would fail deploys
    whenever Neon's compute is suspended, and would turn any uptime monitor
    pointed at this path into a keepalive that prevents suspension. Use
    /health/db when you actually want to know about the database.
    """
    return {
        "status": "healthy",
        "version": API_VERSION,
        "db_connected": None,
        "poller_running": poller_is_running(),
    }


@app.get("/health/db", summary="Database connectivity check")
async def health_db():
    """Real connectivity check.

    WARNING: this opens a connection and therefore *resumes* the Neon
    compute. For humans and low-frequency (daily at most) checks only —
    never point an uptime monitor here.
    """
    db_connected = False
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        db_connected = True
    except Exception as e:
        logger.warning("Database health check failed: %s", e)

    body = {
        "status": "healthy" if db_connected else "degraded",
        "version": API_VERSION,
        "db_connected": db_connected,
    }
    if not db_connected:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)
    return body


@app.post(
    "/v1/internal/poll-batches",
    summary="Force a one-off sweep for completed Message Batches",
)
async def poll_batches(
    x_pipeline_key: str | None = Header(default=None, alias="X-Pipeline-Key"),
):
    """Manual recovery hatch for an orphaned batch.

    Runs a single poll pass and re-arms the background poller if anything is
    still pending. Only relevant if the batch path is revived — the live
    pipeline generates prose synchronously.
    """
    _verify_pipeline_key(x_pipeline_key)

    try:
        pending = await sweep_once()
    except Exception as e:
        logger.error("Manual batch sweep failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Sweep failed: {e}",
        )

    if pending:
        ensure_poller_running()

    return {"pending": pending, "poller_running": poller_is_running()}


@app.post(
    "/v1/run-issue",
    summary="Trigger the weekly LangGraph issue pipeline",
)
async def run_issue(
    request: Request,
    x_pipeline_key: str | None = Header(default=None, alias="X-Pipeline-Key"),
):
    """Kick off the issue_pipeline. The body should be JSON with at minimum
    a `user_id` field (the Clerk-mapped UUID to generate the issue for).

    Returns immediately with the agent_run_id; the workflow runs in the
    background. Poll `/v1/agent/status` (on the read API) for progress.
    """
    _verify_pipeline_key(x_pipeline_key)

    try:
        body = await request.json()
    except Exception:
        body = {}

    user_id = body.get("user_id")
    force = bool(body.get("force", False))
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id required",
        )

    run_id = str(uuid.uuid4())
    initial: IssueState = {
        "user_id": user_id,
        "agent_run_id": run_id,
        "force": force,
        "errors": [],
    }

    async def _run():
        try:
            await issue_pipeline.ainvoke(initial)
            logger.info("Issue pipeline completed for run %s", run_id)
        except Exception as e:
            logger.error("Issue pipeline failed for run %s: %s", run_id, e)

    asyncio.create_task(_run())

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"agent_run_id": run_id, "status": "running"},
    )


@app.post(
    "/v1/friday-drop",
    summary="Deliver the Friday surprise email for one user",
)
async def friday_drop(
    request: Request,
    x_pipeline_key: str | None = Header(default=None, alias="X-Pipeline-Key"),
):
    """Render + send every pending Friday surprise for `user_id`.

    Unlike /v1/run-issue, this runs synchronously — the work is small
    (one or two emails per user, typically one), and the caller (Vercel
    cron route) wants a real summary back so it can roll up alerts. No
    background task. Resend rate limits permitting, the whole thing
    finishes in under a few seconds.
    """
    _verify_pipeline_key(x_pipeline_key)

    try:
        body = await request.json()
    except Exception:
        body = {}

    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id required",
        )

    summary = await deliver_friday_drops_for_user(user_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=summary,
    )


@app.post(
    "/v1/build-taste-profile",
    summary="Embed seed artists + compute taste centroid for a user",
)
async def build_taste_profile(
    request: Request,
    x_pipeline_key: str | None = Header(default=None, alias="X-Pipeline-Key"),
):
    """Read taste_profiles.seed for the given user, run it through
    build_profile_from_seed (Voyage embedding + tag derivation), and
    upsert the centroid + tag weights into the same row.

    Called by the web app's /api/onboarding handler after a new user
    submits their seed. Idempotent: re-running with the same seed
    produces the same centroid and overwrites the prior row.

    Runs synchronously — the Voyage call is one round-trip and the
    caller fires-and-forgets anyway, so there's no batch to manage.
    """
    _verify_pipeline_key(x_pipeline_key)

    try:
        body = await request.json()
    except Exception:
        body = {}

    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id required",
        )

    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT seed::text AS seed FROM taste_profiles WHERE user_id = $1::uuid",
        user_id,
    )
    if row is None or not row["seed"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No seed found for user_id={user_id}",
        )

    import json
    try:
        seed = json.loads(row["seed"])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored seed is not valid JSON",
        )

    try:
        profile = await build_profile_from_seed(seed)
        await upsert_taste_profile(pool, user_id, profile)
    except Exception as e:
        logger.error("build_taste_profile failed for %s: %s", user_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Profile build failed: {e}",
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "user_id": user_id,
            "n_artists": len(seed.get("artists", [])),
            "n_tags": len(seed.get("tags", [])),
        },
    )


@app.post(
    "/v1/parse-playlist",
    summary="Extract artist list from a public Spotify playlist URL",
)
async def parse_playlist(
    request: Request,
    x_pipeline_key: str | None = Header(default=None, alias="X-Pipeline-Key"),
):
    """Given a Spotify playlist URL/ID, return up to 50 unique artist
    names from the playlist's tracks. Used by the onboarding form to
    pre-fill the artists textarea so users can paste a playlist they
    already curate instead of typing 20+ names manually.

    Only public playlists work — we use Client Credentials, not user
    OAuth. Private playlists return an empty list (treated as "not
    found" by the caller, who falls back to manual entry).
    """
    _verify_pipeline_key(x_pipeline_key)

    try:
        body = await request.json()
    except Exception:
        body = {}

    raw = body.get("playlist_url") or body.get("url") or ""
    playlist_id = parse_playlist_url(raw)
    if not playlist_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't recognize a Spotify playlist URL or ID in the input",
        )

    artists = await fetch_playlist_artists(playlist_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"playlist_id": playlist_id, "artists": artists},
    )


@app.post(
    "/v1/sync-spotify-playlist",
    summary="Write this week's picks into a user's Crate Digger Spotify playlist",
)
async def sync_spotify_playlist(
    request: Request,
    x_pipeline_key: str | None = Header(default=None, alias="X-Pipeline-Key"),
):
    """Tier 3: per-user playlist write-back.

    Body: `{user_id: str}` syncs one user; `{all: true}` enumerates every
    connected user (used by the Sunday cron post-issue hook).

    Per-user errors don't crash — see sync_playlist_for_user / sync_for_all
    for the failure-mode summary contract. Always returns 200 with a
    structured outcome.
    """
    _verify_pipeline_key(x_pipeline_key)

    try:
        body = await request.json()
    except Exception:
        body = {}

    if body.get("all"):
        results = await sync_for_all_connected_users()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"results": results, "total": len(results)},
        )

    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id required (or pass {all: true})",
        )

    result = await sync_playlist_for_user(user_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"user_id": user_id, **result},
    )
