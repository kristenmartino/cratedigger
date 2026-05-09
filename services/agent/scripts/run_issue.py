"""Manually trigger one issue generation. Used in dev and as the cron entry.

Usage:
    python scripts/run_issue.py --user-id <uuid>
    python scripts/run_issue.py --clerk-id user_kristen_seed_v1
    python scripts/run_issue.py --email krissi889@gmail.com --force
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

from agent.db import close_pool, get_pool, init_pool  # noqa: E402
from agent.runs import ensure_run, mark_failed  # noqa: E402
from agent.workflows.issue_workflow import IssueState, issue_pipeline  # noqa: E402

logger = logging.getLogger("cratedigger-agent.run_issue")


async def _resolve_user_id(args: argparse.Namespace) -> str:
    if args.user_id:
        return args.user_id
    if not (args.clerk_id or args.email):
        raise SystemExit("Provide one of --user-id, --clerk-id, or --email.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        if args.clerk_id:
            row = await conn.fetchrow(
                "SELECT id::text FROM users WHERE clerk_id = $1", args.clerk_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT id::text FROM users WHERE email = $1", args.email,
            )
    if not row:
        raise SystemExit(f"No user matched {args.clerk_id or args.email}.")
    return row["id"]


async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO)
    await init_pool()
    run_id = str(uuid.uuid4())
    try:
        user_id = await _resolve_user_id(args)
        await ensure_run(run_id)
        initial: IssueState = {
            "user_id": user_id,
            "agent_run_id": run_id,
            "force": bool(args.force),
            "errors": [],
        }
        try:
            result = await issue_pipeline.ainvoke(initial)
        except Exception as e:
            logger.exception("Issue pipeline crashed")
            await mark_failed(run_id, str(e))
            raise
        logger.info("Issue pipeline completed: %s", {
            "issue_id": result.get("issue_id"),
            "errors": result.get("errors", []),
        })
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one Crate Digger issue.")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--user-id", help="Postgres users.id (UUID)")
    g.add_argument("--clerk-id", help="users.clerk_id (e.g. user_kristen_seed_v1)")
    g.add_argument("--email", help="users.email")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the duplicate-day issue check")
    asyncio.run(main(parser.parse_args()))
