"""asyncpg pool for the agent. Same shape as services/api/app/db.py.

The agent runs as a one-shot worker (Saturday cron), so the pool is created
in the workflow entry point and torn down at completion. Long-running daemons
(if added later) should follow the api pattern with lifespan management.
"""
from __future__ import annotations

import asyncpg

from agent.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=5,  # Neon hobby/free cap; same as api
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        return await init_pool()
    return _pool
