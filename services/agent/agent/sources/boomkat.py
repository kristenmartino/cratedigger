"""Boomkat scraper.

Boomkat doesn't publish a clean RSS feed (per SPEC.md §3.2). We scrape
boomkat.com/new-this-week and follow product detail pages. Be respectful:
1 req/sec rate limit, real User-Agent, robots.txt-respecting.

This is a stub. Boomkat's HTML structure changes occasionally; expect
this scraper to break and need maintenance.
"""
from __future__ import annotations

import logging

import httpx
from selectolax.parser import HTMLParser

from agent.config import settings
from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.boomkat")

BASE_URL = "https://boomkat.com"
INDEX_PATH = "/new-this-week"


async def scrape_boomkat() -> list[RawRelease]:
    """Scrape Boomkat's new-this-week page. Returns RawReleases."""
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as http:
            resp = await http.get(BASE_URL + INDEX_PATH)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error("Boomkat scrape failed: %s", e)
        return []

    tree = HTMLParser(html)

    # TODO(week-2): selector logic for boomkat.com/new-this-week.
    # Inspect the live page DOM at scaffold time to find the product
    # card class. Until then this returns [].
    out: list[RawRelease] = []
    cards = tree.css(".product-card")  # TODO update selector
    logger.info("Boomkat scrape stub: found %d product cards (parser not implemented)", len(cards))
    return out
