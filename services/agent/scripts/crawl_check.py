"""Dry-run crawl: hit every active source once and print what came back.

No DB writes, no email. Useful for validating new scrapers and RSS rows
after sources.json changes — invoke after deploying to confirm selectors
haven't drifted and feeds are reachable.

By default, no LLM calls either. Pass `--extract` to additionally run the
production LLM extraction step against any empty-artist entries and print
how many got recovered as releases vs. dropped as news. Requires
ANTHROPIC_API_KEY in env; gracefully degrades to a warning if unset.

Usage:
    python scripts/crawl_check.py            # all active sources
    python scripts/crawl_check.py boomkat    # one source by slug
    python scripts/crawl_check.py --rss-only # skip scrapers
    python scripts/crawl_check.py --scrape-only
    python scripts/crawl_check.py --extract  # adds the LLM extraction pass

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
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Make the agent package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.extract import extract_releases  # noqa: E402
from agent.sources import SCRAPERS  # noqa: E402
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
    # Populated by crawl_one: how many releases have non-empty artist (clean
    # path) vs empty (would need LLM extraction in the real pipeline).
    with_artist: int = 0
    empty_artist: int = 0
    # All releases — kept for the optional --extract pass. Not printed in the
    # default report; the 3-item `samples` is.
    all_releases: list[RawRelease] = field(default_factory=list)


@dataclass
class ExtractStats:
    """Per-source totals from running extract_releases over empty-artist entries."""
    slug: str
    submitted: int
    kept: int
    dropped: int
    samples: list[tuple[str, str]]  # (extracted_artist, extracted_title)


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

    with_artist = sum(1 for r in releases if (r.artist or "").strip())
    return SourceResult(
        slug=slug,
        name=source["name"],
        method=source["ingest_method"],
        elapsed_s=time.monotonic() - started,
        count=len(releases),
        samples=releases[:3],
        with_artist=with_artist,
        empty_artist=len(releases) - with_artist,
        all_releases=releases,
    )


def _format_release(r: RawRelease) -> str:
    artist = r.artist or "(no artist)"
    title = r.title or "(no title)"
    return f"  · {artist} — {title}"


def print_report(results: list[SourceResult]) -> None:
    total_releases = sum(r.count for r in results)
    total_with_artist = sum(r.with_artist for r in results)
    total_empty = sum(r.empty_artist for r in results)
    healthy = sum(1 for r in results if r.error is None and r.count > 0)
    empty = sum(1 for r in results if r.error is None and r.count == 0)
    errored = sum(1 for r in results if r.error is not None)

    print()
    print("=" * 78)
    print(f"  CRAWL CHECK: {len(results)} sources, {total_releases} releases total")
    print(f"  healthy: {healthy}   empty: {empty}   errored: {errored}")
    print(
        f"  with-artist: {total_with_artist}   "
        f"empty-artist: {total_empty} (would need LLM extraction)"
    )
    print("=" * 78)
    print()
    print(
        f"{'method':7} {'slug':24} {'total':>5} {'artist':>6} {'empty':>5}  "
        f"{'time':>7}  status"
    )
    print("-" * 78)
    for r in sorted(results, key=lambda x: (x.method, x.slug)):
        status = "ERROR" if r.error else ("EMPTY" if r.count == 0 else "ok")
        print(
            f"{r.method:7} {r.slug:24} {r.count:>5} "
            f"{r.with_artist:>6} {r.empty_artist:>5}  "
            f"{r.elapsed_s:>6.2f}s  {status}"
        )
        if r.error:
            print(f"  ! {r.error}")
        for sample in r.samples:
            print(_format_release(sample))
    print()


async def run_extraction(results: list[SourceResult]) -> list[ExtractStats]:
    """Run the LLM extraction over every empty-artist entry, grouped by source.

    We run one extract_releases call per source (rather than one global call)
    so the reporting can attribute kept/dropped counts back to the source.
    Cost stays the same — same number of LLM tokens, just batched differently.
    """
    stats: list[ExtractStats] = []
    for r in results:
        empty_entries = [
            asdict(e) for e in r.all_releases if not (e.artist or "").strip()
        ]
        if not empty_entries:
            continue
        enriched = await extract_releases(empty_entries)
        kept = [e for e in enriched if e.get("is_release")]
        samples = [(e["artist"], e["title"]) for e in kept[:3]]
        stats.append(
            ExtractStats(
                slug=r.slug,
                submitted=len(empty_entries),
                kept=len(kept),
                dropped=len(empty_entries) - len(kept),
                samples=samples,
            )
        )
    return stats


def print_extraction_report(stats: list[ExtractStats]) -> None:
    if not stats:
        print("No empty-artist entries to extract — skipping.")
        return

    total_submitted = sum(s.submitted for s in stats)
    total_kept = sum(s.kept for s in stats)
    total_dropped = sum(s.dropped for s in stats)

    print()
    print("=" * 78)
    print(
        f"  LLM EXTRACTION: {total_submitted} entries submitted, "
        f"{total_kept} kept as releases, {total_dropped} dropped as news"
    )
    if total_submitted:
        print(f"  recovery rate: {total_kept / total_submitted:.0%}")
    print("=" * 78)
    print()
    print(f"{'slug':24} {'submitted':>10} {'kept':>5} {'dropped':>8}")
    print("-" * 78)
    for s in sorted(stats, key=lambda x: x.slug):
        print(f"{s.slug:24} {s.submitted:>10} {s.kept:>5} {s.dropped:>8}")
        for artist, title in s.samples:
            print(f"  · {artist} — {title}")
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
    parser.add_argument(
        "--extract",
        action="store_true",
        help=(
            "After the crawl, run the production LLM extraction step against "
            "every empty-artist entry and print a per-source recovery report. "
            "Requires ANTHROPIC_API_KEY; degrades gracefully without it."
        ),
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

    if args.extract:
        stats = await run_extraction(results)
        print_extraction_report(stats)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
