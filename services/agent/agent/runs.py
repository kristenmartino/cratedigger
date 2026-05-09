"""Helpers for incremental writes to the agent_runs table.

The pipeline updates current_step / current_source / counts as it runs so the
"now digging" widget on the archive can show live progress. Pattern adapted
from Sift's pipeline_status.py — same shape, different fields.
"""
from __future__ import annotations

import logging
from typing import Any

from agent.db import get_pool

logger = logging.getLogger("cratedigger-agent.runs")


_ALLOWED_FIELDS = {
    "issue_id",
    "completed_at",
    "status",
    "current_step",
    "current_source",
    "sources_scanned",
    "releases_scanned",
    "candidates_considered",
    "records_surfaced",
    "notes",
}


async def ensure_run(run_id: str) -> None:
    """Insert a row for this agent_run_id if one doesn't already exist.
    Idempotent — safe to call from the entry node every time."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_runs (id, status, current_step)
            VALUES ($1, 'running', 'starting')
            ON CONFLICT (id) DO NOTHING
            """,
            run_id,
        )


async def update_agent_run(run_id: str, **fields: Any) -> None:
    """UPDATE only the supplied fields. Silently drops unknown keys."""
    cleaned = {k: v for k, v in fields.items() if k in _ALLOWED_FIELDS}
    if not cleaned:
        return

    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cleaned))
    args = [run_id, *cleaned.values()]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE agent_runs SET {sets} WHERE id = $1", *args)


async def increment_counters(run_id: str, **counters: int) -> None:
    """Atomic increment for the *_scanned / *_considered / *_surfaced fields."""
    cleaned = {k: v for k, v in counters.items() if k in _ALLOWED_FIELDS and isinstance(v, int)}
    if not cleaned:
        return
    sets = ", ".join(f"{k} = {k} + ${i + 2}" for i, k in enumerate(cleaned))
    args = [run_id, *cleaned.values()]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE agent_runs SET {sets} WHERE id = $1", *args)


async def mark_completed(run_id: str, issue_id: str | None = None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_runs
               SET status = 'completed',
                   current_step = 'done',
                   completed_at = NOW(),
                   issue_id = COALESCE($2, issue_id)
             WHERE id = $1
            """,
            run_id, issue_id,
        )


async def mark_failed(run_id: str, error: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_runs
               SET status = 'failed',
                   completed_at = NOW(),
                   notes = $2
             WHERE id = $1
            """,
            run_id, error[:2000],
        )
