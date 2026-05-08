"""Manually trigger one issue generation. Used in dev and as the cron entry.

Usage:
    python scripts/run_issue.py --user kristen --issue 5
    python scripts/run_issue.py --user kristen --force
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

# Make the agent package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.db import close_pool, init_pool  # noqa: E402
from agent.workflows.issue_workflow import IssueState, issue_pipeline  # noqa: E402

logger = logging.getLogger("cratedigger-agent.run_issue")


async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO)
    await init_pool()
    try:
        # TODO: resolve user by Clerk username/slug or email
        user_id = args.user_id or str(uuid.uuid4())  # placeholder
        run_id = str(uuid.uuid4())

        initial: IssueState = {
            "user_id": user_id,
            "agent_run_id": run_id,
            "force": args.force,
            "errors": [],
        }
        result = await issue_pipeline.ainvoke(initial)
        logger.info("Issue pipeline completed: %s", {
            "issue_id": result.get("issue_id"),
            "errors": result.get("errors", []),
        })
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one Crate Digger issue.")
    parser.add_argument("--user-id", help="Clerk user_id to generate the issue for")
    parser.add_argument("--issue", type=int, help="Issue number (auto-incremented if omitted)")
    parser.add_argument("--force", action="store_true", help="Bypass dedup")
    args = parser.parse_args()
    asyncio.run(main(args))
