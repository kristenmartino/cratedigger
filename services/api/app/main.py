"""Crate Digger API. FastAPI shell harvested from Sift's app/main.py.

Differences from Sift:
  - No in-process `_scheduled_refresh()` task (CD uses Vercel cron / Railway
    scheduled jobs to trigger the agent on Saturday night, not lifespan).
  - No batch poller in lifespan either — that lives in services/agent.
  - CORS allowlist comes from settings.cors_origins.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.db import close_pool, get_pool, init_pool
from app.dependencies import limiter
from app.models import HealthResponse

logger = logging.getLogger("cratedigger-api")

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.info("Starting cratedigger-api (env=%s)", settings.environment)

    if settings.pipeline_api_key in ("dev-key", "change-me-in-production", ""):
        logger.warning(
            "SECURITY: PIPELINE_API_KEY is set to a default/empty value. "
            "Set a strong, unique key via the PIPELINE_API_KEY environment variable."
        )

    try:
        await init_pool()
        logger.info("Database pool initialized")
    except Exception as e:
        logger.warning("Failed to connect to database: %s", e)

    yield

    await close_pool()
    logger.info("cratedigger-api shut down")


app = FastAPI(
    title="Crate Digger API",
    version=API_VERSION,
    description=(
        "Read/write API for Crate Digger. Backs the editorial digest, archive, "
        "and feedback loop. The agent (services/agent) writes issues via "
        "internal endpoints; the web app reads issues + posts feedback via "
        "public endpoints."
    ),
    lifespan=lifespan,
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


# ── Security headers (harvested from Sift verbatim) ──────────────────────

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
        response.headers["Permissions-Policy"] = "()"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Language"] = "en"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Pipeline-Key"],
)


# ── Routers ──────────────────────────────────────────────────────────────

# Routers are stubs for now; flesh out per the SPEC.md §5 API surface.
# Versioned mount prefix matches Sift convention: /v1/*
# from app.routers import issues, archive, agent, feedback, annotations, pipeline
# app.include_router(issues.router, prefix="/v1")
# app.include_router(archive.router, prefix="/v1")
# app.include_router(agent.router, prefix="/v1")
# app.include_router(feedback.router, prefix="/v1")
# app.include_router(annotations.router, prefix="/v1")
# app.include_router(pipeline.router, prefix="/v1/internal")


# ── Service-info + health ────────────────────────────────────────────────

@app.get("/", summary="Service info")
async def root():
    return {
        "service": "cratedigger-api",
        "version": API_VERSION,
        "endpoints": {
            "health": "GET /health",
            "issues": "GET /v1/issues/:number",
            "archive": "GET /v1/archive",
            "agent_status": "GET /v1/agent/status",
            "feedback": "POST /v1/feedback",
            "docs": "GET /docs",
        },
    }


@app.get("/health", response_model=HealthResponse, summary="Health check")
async def health():
    db_connected = False
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        db_connected = True
    except Exception:
        pass

    return HealthResponse(
        status="healthy" if db_connected else "degraded",
        version=API_VERSION,
        db_connected=db_connected,
    )
