"""Dry-run crawl: hit every active source once and print what came back.

No DB writes, no LLM calls, no email. Useful for validating new scrapers
and RSS rows after sources.json changes — invoke after deploying to confirm
selectors haven't drifted and feeds are reachable.

Usage:
    python scripts/crawl_check.py            # all active sources
    python scripts/crawl_check.py boomkat    # one source by slug
    python scripts/crawl_check.py --rss-only # skip scrapers
    python scripts/crawl_check.py --scrape-only

Exit code is always 0 — a single source returning zero releases is logged but
doesn't fail the script. Read the printed table to triage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Make the agent package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.sources import SCRAPERS, get_api_fetcher  # noqa: E402
from agent.sources.rss import RawRelease, fetch_rss  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("cratedigger-agent.crawl_check")


@dataclass
class SourceResult:
    slug: str
    name: str
    method: str
    elapsed_s: float
    count: int
    samples: list[RawRelease]
    error: str | None = None


async def crawl_one(source: dict) -> SourceResult:
    slug = source["slug"]
    started = time.monotonic()
    try:
        if source["ingest_method"] == "rss":
            releases = await fetch_rss(slug, source["ingest_url"])
        elif source["ingest_method"] == "scrape":
            scraper = SCRAPERS.get(slug)
            if scraper is None:
                return SourceResult(
                    slug=slug,
                    name=source["name"],
                    method=source["ingest_method"],
                    elapsed_s=0.0,
                    count=0,
                    samples=[],
                    error=f"no scraper registered for slug {slug!r}",
                )
            releases = await scraper()
        elif source["ingest_method"] == "api":
            fetcher = get_api_fetcher(source["ingest_url"])
            if fetcher is None:
                return SourceResult(
                    slug=slug,
                    name=source["name"],
                    method=source["ingest_method"],
                    elapsed_s=0.0,
                    count=0,
                    samples=[],
                    error=f"no api fetcher matched url {source['ingest_url']!r}",
                )
            releases = await fetcher(slug, source["ingest_url"])
        else:
            return SourceResult(
                slug=slug,
                name=source["name"],
                method=source["ingest_method"],
                elapsed_s=0.0,
                count=0,
                samples=[],
                error=f"unknown ingest_method {source['ingest_method']!r}",
            )
    except Exception as e:
        return SourceResult(
            slug=slug,
            name=source["name"],
            method=source["ingest_method"],
            elapsed_s=time.monotonic() - started,
            count=0,
            samples=[],
            error=f"{type(e).__name__}: {e}",
        )

    return SourceResult(
        slug=slug,
        name=source["name"],
        method=source["ingest_method"],
        elapsed_s=time.monotonic() - started,
        count=len(releases),
        samples=releases[:3],
    )


def _format_release(r: RawRelease) -> str:
    artist = r.artist or "(no artist)"
    title = r.title or "(no title)"
    return f"  · {artist} — {title}"


def print_report(results: list[SourceResult]) -> None:
    total_releases = sum(r.count for r in results)
    healthy = sum(1 for r in results if r.error is None and r.count > 0)
    empty = sum(1 for r in results if r.error is None and r.count == 0)
    errored = sum(1 for r in results if r.error is not None)

    print()
    print("=" * 78)
    print(f"  CRAWL CHECK: {len(results)} sources, {total_releases} releases total")
    print(f"  healthy: {healthy}   empty: {empty}   errored: {errored}")
    print("=" * 78)
    print()
    print(f"{'method':7} {'slug':24} {'count':>6}  {'time':>7}  status")
    print("-" * 78)
    for r in sorted(results, key=lambda x: (x.method, x.slug)):
        status = "ERROR" if r.error else ("EMPTY" if r.count == 0 else "ok")
        print(
            f"{r.method:7} {r.slug:24} {r.count:>6}  {r.elapsed_s:>6.2f}s  {status}"
        )
        if r.error:
            print(f"  ! {r.error}")
        for sample in r.samples:
            print(_format_release(sample))
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "slug", nargs="?", default=None, help="Optional: only crawl this one slug"
    )
    parser.add_argument(
        "--rss-only", action="store_true", help="Only hit RSS sources"
    )
    parser.add_argument(
        "--scrape-only", action="store_true", help="Only hit scrape sources"
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also crawl rows with active=false",
    )
    args = parser.parse_args()

    sources = json.loads((DATA_DIR / "sources.json").read_text())
    if not args.include_inactive:
        sources = [s for s in sources if s.get("active", True)]
    if args.slug:
        sources = [s for s in sources if s["slug"] == args.slug]
        if not sources:
            print(f"No source matches slug {args.slug!r}", file=sys.stderr)
            return 2
    if args.rss_only:
        sources = [s for s in sources if s["ingest_method"] == "rss"]
    if args.scrape_only:
        sources = [s for s in sources if s["ingest_method"] == "scrape"]

    if not sources:
        print("No sources to crawl after filtering", file=sys.stderr)
        return 2

    logger.info("Crawling %d sources", len(sources))
    results = await asyncio.gather(*(crawl_one(s) for s in sources))
    print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
