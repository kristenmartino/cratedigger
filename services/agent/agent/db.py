"""asyncpg pool for the agent. Same shape as services/api/app/db.py.

Neon bills compute by *active time*, and a compute endpoint only suspends
after a window with zero open connections. So the pool is deliberately
lazy and self-reaping: nothing is opened until a query needs it
(`min_size=0`), and the last connection is dropped 30s after the final
query (`max_inactive_connection_lifetime`). Never call `init_pool()` from
a lifespan — that pins a connection for the life of the process and Neon
never idles.
"""
from __future__ import annotations

import asyncio

import asyncpg

from agent.config import settings

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
                max_size=5,  # Neon hobby/free cap; same as api
                max_inactive_connection_lifetime=IDLE_CONNECTION_LIFETIME_SECONDS,
                # A hung query must not pin a connection indefinitely.
                command_timeout=30.0,
                # A slow Neon resume should surface as an error, not a hang.
                timeout=10.0,
                # Makes `pg_stat_activity` legible when auditing for leaks.
                server_settings={"application_name": "cratedigger-agent"},
            )
    return _pool


async def close_pool() -> None:
    global _pool
    async with _pool_lock:
        if _pool:
            await _pool.close()
            _pool = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        return await init_pool()
    return _pool
