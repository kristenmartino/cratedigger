"""Event-armed poller for Anthropic Message Batches.

This used to be an unconditional `while True:` loop on a 60s interval,
started in the FastAPI lifespan whenever ENVIRONMENT=production. That issued
~43k queries/month against `api_batches` — which meant Neon's compute could
never autosuspend. It was the single largest driver of the project's Neon
bill, and it was pure waste: the live pipeline generates prose with
`prose.generate_prose_live`, `prose.submit_prose_batch` has no callers, and
so the table it polled has always been empty.

The poller now only runs when a batch genuinely exists. It is armed by an
event — `submit_batch()`, or a process start that finds pending rows, or a
manual sweep — and it **exits once nothing is pending**, so an idle
deployment issues zero queries and Neon can suspend.

Run standalone for debugging:
    python -m agent.batch_poller
"""
from __future__ import annotations

import asyncio
import logging
import time

from agent.batch_client import poll_pending_batches

logger = logging.getLogger("cratedigger-agent.batch_poller")

# Escalating backoff: attentive while a batch is likely to land, then cheap.
# (elapsed_seconds_threshold, sleep_seconds)
BACKOFF_SCHEDULE: tuple[tuple[float, float], ...] = (
    (10 * 60, 60),        # first 10 min: every minute
    (70 * 60, 5 * 60),    # next hour: every 5 min
)
BACKOFF_FALLBACK_SECONDS = 15 * 60  # thereafter: every 15 min

# Anthropic's batch SLA is 24h. Past this, stop looping — `poll_pending_batches`
# marks the stale rows terminal so they can never re-arm the loop forever.
MAX_POLL_DURATION_SECONDS = 26 * 60 * 60

_task: asyncio.Task | None = None


# Kind → async handler(batch_id, results_list)
# Stubs for now; flesh out as each generator lands.
async def _todo_handler(batch_id: str, results: list[dict]) -> None:
    """Placeholder handler.

    This RAISES rather than returning. `poll_pending_batches` marks a batch
    'succeeded' when its handler returns cleanly, so a handler that quietly
    did nothing would cause real, already-paid-for results to be fetched,
    discarded, and the row closed out — unrecoverable. Raising routes the
    batch to 'needs_attention' with its results_url intact.
    """
    raise NotImplementedError(
        f"No handler implemented for batch {batch_id} ({len(results)} results). "
        "Results are retained by Anthropic for 29 days and the api_batches row "
        "is marked needs_attention — implement the handler and re-run the sweep."
    )


HANDLERS = {
    "prose": _todo_handler,           # editorial paragraph per recommendation
    "signals": _todo_handler,         # matched-signals tag list per recommendation
    "pull_quote": _todo_handler,      # 4-8 word phrase from the lead's prose
    "editor_note": _todo_handler,     # the issue's editor note (single call)
}


def _sleep_for(elapsed: float) -> float:
    for threshold, interval in BACKOFF_SCHEDULE:
        if elapsed < threshold:
            return interval
    return BACKOFF_FALLBACK_SECONDS


def ensure_poller_running() -> bool:
    """Start the recovery loop if it isn't already running.

    Idempotent and safe to call from anywhere — a second call while the loop
    is live is a no-op. Returns True if this call started the loop.
    """
    global _task
    if _task is not None and not _task.done():
        return False
    _task = asyncio.create_task(run_batch_poller())
    logger.info("Batch poller armed")
    return True


def poller_is_running() -> bool:
    return _task is not None and not _task.done()


async def run_batch_poller() -> None:
    """Poll until nothing is pending, then exit.

    Exiting is the point: a loop that keeps querying an empty table is what
    kept Neon awake. Survives individual iteration errors.
    """
    global _task
    started = time.monotonic()
    logger.info("Batch poller started")
    try:
        while True:
            elapsed = time.monotonic() - started
            try:
                pending = await poll_pending_batches(
                    HANDLERS,
                    stale_after_seconds=MAX_POLL_DURATION_SECONDS,
                )
            except asyncio.CancelledError:
                logger.info("Batch poller cancelled")
                raise
            except Exception as e:
                # Unknown pending count — keep looping, the deadline still bounds us.
                logger.error("Batch poller iteration failed: %s", e)
                pending = None

            if pending == 0:
                logger.info("Batch poller idle — nothing pending, exiting")
                return

            if elapsed > MAX_POLL_DURATION_SECONDS:
                logger.error(
                    "Batch poller exceeded %.0fh with %s still pending — exiting. "
                    "Rows past the staleness window are marked needs_attention.",
                    MAX_POLL_DURATION_SECONDS / 3600,
                    pending,
                )
                return

            await asyncio.sleep(_sleep_for(elapsed))
    finally:
        _task = None


async def sweep_once() -> int | None:
    """Run a single poll pass. Returns the number of rows still processing.

    Backs the manual `POST /v1/internal/poll-batches` recovery endpoint.
    """
    return await poll_pending_batches(
        HANDLERS,
        stale_after_seconds=MAX_POLL_DURATION_SECONDS,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_batch_poller())
