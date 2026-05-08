"""Background poller for Anthropic Message Batches.

Pattern harvested from sift-api/services/batch_poller.py. The HANDLERS dict
maps batch kinds to async handlers — Sift had context/entity/primer kinds;
Crate Digger has prose/signals/pull_quote/editor_note kinds.

Run via:
    python -m agent.batch_poller

In production this is a separate Railway service (or a long-running scheduled
task, depending on how the weekly pipeline is structured).
"""
from __future__ import annotations

import asyncio
import logging

from agent.batch_client import poll_pending_batches

logger = logging.getLogger("cratedigger-agent.batch_poller")

POLL_INTERVAL_SECONDS = 60


# Kind → async handler(batch_id, results_list)
# Stubs for now; flesh out as each generator lands.
async def _todo_handler(batch_id: str, results: list[dict]) -> None:
    logger.warning(
        "Stub handler invoked for batch %s with %d results — replace with the real handler",
        batch_id,
        len(results),
    )


HANDLERS = {
    "prose": _todo_handler,           # editorial paragraph per recommendation
    "signals": _todo_handler,         # matched-signals tag list per recommendation
    "pull_quote": _todo_handler,      # 4-8 word phrase from the lead's prose
    "editor_note": _todo_handler,     # the issue's editor note (single call)
}


async def run_batch_poller() -> None:
    """Poll loop. Survives individual iteration errors."""
    logger.info("Batch poller started (interval=%ds)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await poll_pending_batches(HANDLERS)
        except asyncio.CancelledError:
            logger.info("Batch poller cancelled")
            raise
        except Exception as e:
            logger.error("Batch poller iteration failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_batch_poller())
