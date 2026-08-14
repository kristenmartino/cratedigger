"""Anthropic Message Batches wrapper. Harvested verbatim from
sift-api/services/batch_client.py.

Batches get a flat 50% discount on input + output tokens vs the realtime
Messages API, at the cost of up to 24h SLA (typically minutes).

Status: the live pipeline does NOT use this path. `issue_workflow`'s
generate_prose_node calls `prose.generate_prose_live`, and
`prose.submit_prose_batch` has no callers — so `api_batches` stays empty in
production. That matters because `batch_poller` used to query this table
every 60 seconds regardless, which is what kept Neon's compute from ever
suspending.

If the batch path is revived, `submit_batch` arms the poller and the poller
exits once nothing is pending, so an idle deployment issues zero queries.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

import anthropic
import httpx

from agent.config import settings
from agent.db import get_pool

logger = logging.getLogger("cratedigger-agent.batch_client")

MODEL = "claude-haiku-4-5-20251001"


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def submit_batch(kind: str, requests: list[dict], metadata: dict | None = None) -> str | None:
    """Submit a batch of message requests and record it in api_batches.

    Each request must have {'custom_id': str, 'params': {model, max_tokens, messages, ...}}.
    Returns the Anthropic batch_id, or None if submission failed.
    """
    if not requests:
        return None

    client = _client()
    try:
        batch = await client.messages.batches.create(requests=requests)
    except Exception as e:
        logger.error("submit_batch(%s) failed: %s", kind, e)
        return None

    pool = await get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO api_batches (batch_id, kind, status, metadata)
            VALUES ($1, $2, 'processing', $3::jsonb)
            ON CONFLICT (batch_id) DO NOTHING
            """,
            batch.id,
            kind,
            json.dumps(metadata or {}),
        )
    except Exception as e:
        logger.error("Failed to record batch %s in api_batches: %s", batch.id, e)

    logger.info(json.dumps({
        "event": "batch_submitted",
        "kind": kind,
        "batch_id": batch.id,
        "requests": len(requests),
    }))

    # Arm the recovery poller so this batch is collected even if the run that
    # submitted it dies before `await_batch()` returns. Imported here rather
    # than at module scope — batch_poller imports from this module.
    try:
        from agent.batch_poller import ensure_poller_running

        ensure_poller_running()
    except RuntimeError:
        # No running event loop (e.g. a sync CLI context) — the startup
        # re-arm in server.py covers this batch on the next boot.
        logger.debug("No event loop to arm the batch poller on")

    return batch.id


async def count_pending_batches(within_seconds: float = 48 * 60 * 60) -> int:
    """Rows still in 'processing', bounded to recent submissions.

    Used once at process start to decide whether the recovery poller needs
    arming. The window matters: without it, one permanently-stuck row would
    re-arm the poller on every boot forever.
    """
    pool = await get_pool()
    return await pool.fetchval(
        """
        SELECT count(*) FROM api_batches
        WHERE status = 'processing'
          AND submitted_at > now() - ($1 * interval '1 second')
        """,
        within_seconds,
    )


async def poll_pending_batches(
    handlers: dict[str, Callable[[str, list[dict]], Awaitable[None]]],
    stale_after_seconds: float | None = None,
) -> int:
    """Poll every row where status='processing'. For each that has ended,
    stream the JSONL results and invoke handlers[kind](batch_id, results).

    Returns the number of rows still 'processing' after this pass — the
    caller uses it to decide whether to keep polling or stop. Returning 0 is
    what lets the poller exit and Neon suspend.

    `stale_after_seconds` closes out rows older than the batch SLA as
    'needs_attention' so a stuck row can't keep a poller alive indefinitely.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT batch_id, kind, submitted_at FROM api_batches "
        "WHERE status = 'processing' ORDER BY submitted_at"
    )
    if not rows:
        return 0

    now = datetime.now(timezone.utc)
    still_pending = 0
    client = _client()
    for row in rows:
        batch_id = row["batch_id"]
        kind = row["kind"]

        if stale_after_seconds is not None:
            age = (now - row["submitted_at"]).total_seconds()
            if age > stale_after_seconds:
                logger.error(
                    "Batch %s (kind=%s) still processing after %.1fh — marking "
                    "needs_attention. Results stay retrievable from Anthropic.",
                    batch_id, kind, age / 3600,
                )
                await _mark_status(pool, batch_id, "needs_attention")
                continue

        try:
            batch = await client.messages.batches.retrieve(batch_id)
        except Exception as e:
            logger.error("batches.retrieve(%s) failed: %s", batch_id, e)
            still_pending += 1
            continue

        if batch.processing_status != "ended":
            still_pending += 1
            continue

        results_url = getattr(batch, "results_url", None)
        if not results_url:
            logger.error("Batch %s ended but has no results_url", batch_id)
            await _mark_status(pool, batch_id, "errored")
            continue

        try:
            parsed = await _fetch_results_jsonl(results_url)
        except Exception as e:
            logger.error("Failed to fetch results for %s: %s", batch_id, e)
            still_pending += 1
            continue

        handler = handlers.get(kind)
        if handler is None:
            # NOT 'succeeded' — that would close out a batch whose results
            # were fetched and then dropped on the floor. 'needs_attention'
            # keeps the row recoverable once a handler exists.
            logger.error(
                "No handler registered for batch kind=%s (batch=%s) — marking "
                "needs_attention; results are retained by Anthropic for 29 days",
                kind, batch_id,
            )
            await _mark_status(pool, batch_id, "needs_attention")
            continue

        try:
            await handler(batch_id, parsed)
        except NotImplementedError as e:
            logger.error("Handler for kind=%s batch=%s is a stub: %s", kind, batch_id, e)
            await _mark_status(pool, batch_id, "needs_attention")
            continue
        except Exception as e:
            logger.error("Handler for kind=%s batch=%s failed: %s", kind, batch_id, e)
            await _mark_status(pool, batch_id, "errored")
            continue

        await _mark_status(pool, batch_id, "succeeded")
        logger.info(json.dumps({
            "event": "batch_completed",
            "kind": kind,
            "batch_id": batch_id,
            "results": len(parsed),
        }))

    return still_pending


async def _fetch_results_jsonl(url: str) -> list[dict]:
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
    }
    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.get(url, headers=headers)
        resp.raise_for_status()
        lines = resp.text.splitlines()
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSONL line")
    return out


async def _mark_status(pool, batch_id: str, status: str) -> None:
    try:
        await pool.execute(
            "UPDATE api_batches SET status = $1, completed_at = NOW() WHERE batch_id = $2",
            status, batch_id,
        )
    except Exception as e:
        logger.error("Failed to mark batch %s status=%s: %s", batch_id, status, e)
