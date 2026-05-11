"""Source crawler registry.

Per SPEC.md §3.1, RSS sources are picked up automatically from the `sources`
table — no per-source code needed. Scrapers, by contrast, are bespoke per
site and live as functions in this package.

`SCRAPERS` maps a source slug to its async crawl function so the workflow
can dispatch by name. Adding a scraper = drop a module in this package and
register it here. The slug must match the row in `sources` table /
`data/sources.json`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent.sources.bleep import scrape_bleep
from agent.sources.boomkat import scrape_boomkat
from agent.sources.hardwax import scrape_hardwax
from agent.sources.norman_records import scrape_norman_records
from agent.sources.reddit import fetch_reddit_subreddit
from agent.sources.resident_advisor import scrape_resident_advisor
from agent.sources.rss import RawRelease

ScraperFn = Callable[[], Awaitable[list[RawRelease]]]
ApiFetcher = Callable[[str, str], Awaitable[list[RawRelease]]]

SCRAPERS: dict[str, ScraperFn] = {
    "boomkat": scrape_boomkat,
    "resident-advisor": scrape_resident_advisor,
    "norman-records": scrape_norman_records,
    "hardwax": scrape_hardwax,
    "bleep": scrape_bleep,
}

# ingest_method="api" sources. Routed by URL host substring — currently
# only Reddit, but the shape generalizes: each entry is a (host-marker,
# fetcher) pair where the fetcher takes (slug, ingest_url).
API_FETCHERS: tuple[tuple[str, ApiFetcher], ...] = (
    ("reddit.com", fetch_reddit_subreddit),
)


def get_api_fetcher(url: str) -> ApiFetcher | None:
    for marker, fn in API_FETCHERS:
        if marker in url:
            return fn
    return None


__all__ = [
    "API_FETCHERS",
    "ApiFetcher",
    "RawRelease",
    "SCRAPERS",
    "ScraperFn",
    "get_api_fetcher",
]
