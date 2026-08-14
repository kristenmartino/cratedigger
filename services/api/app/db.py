"""Database pool.

Pattern harvested from Sift's `app/db.py`, with the connection lifecycle
reworked for Neon's serverless compute billing.

Neon bills by *active time* and only suspends a compute endpoint after a
window with zero open connections. So the pool is lazy and self-reaping:
nothing opens until a query needs it (`min_size=0`) and the last connection
is dropped 30s after the final query (`max_inactive_connection_lifetime`).
Do not call `init_pool()` from the FastAPI lifespan — that pins connections
for the life of the process and the compute never idles.

Migrations deliberately do NOT run here. `packages/db/init.sql` is the
fresh-DB source of truth and operator migrations live as numbered files
under `packages/db/migrations/`, applied deliberately rather than by the
request-serving process on every deploy. (`CREATE INDEX CONCURRENTLY`
cannot run inside a transaction block, so it could never have lived in a
startup hook anyway.)

The `current_setting('app.current_user_id')` RLS pattern means per-request
handlers `SET LOCAL` it for queries against feedback / annotations /
taste_profiles — which requires session semantics, so keep this pointed at
Neon's direct (non-pooler) endpoint. If you ever move it to the `-pooler`
host, add `statement_cache_size=0` or asyncpg will raise
`prepared statement "__asyncpg_stmt_x__" already exists`.
"""
from __future__ import annotations

import asyncio

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()

# Well inside any Neon autosuspend window (default 5 min), so the pool is
# never what keeps the compute awake.
IDLE_CONNECTION_LIFETIME_SECONDS = 30.0


async def init_pool() -> asyncpg.Pool:
    """Create the pool if it doesn't exist. Prefer `get_pool()`."""
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                settings.database_url,
                # min_size=0 so an idle process holds no Neon connections.
                # This does not disable pooling — asyncpg still reuses an
                # open connection for back-to-back queries.
                min_size=0,
                # Pool max=5 stays within Neon hobby/free connection cap.
                max_size=5,
                max_inactive_connection_lifetime=IDLE_CONNECTION_LIFETIME_SECONDS,
                # A hung query must not pin a connection indefinitely.
                command_timeout=30.0,
                # A slow Neon resume should surface as an error, not a hang.
                timeout=10.0,
                # Makes `pg_stat_activity` legible when auditing for leaks.
                server_settings={"application_name": "cratedigger-api"},
            )
    return _pool


async def get_pool() -> asyncpg.Pool:
    """Lazily create the pool on first use."""
    if _pool is None:
        return await init_pool()
    return _pool


async def close_pool() -> None:
    global _pool
    async with _pool_lock:
        if _pool:
            await _pool.close()
            _pool = None
