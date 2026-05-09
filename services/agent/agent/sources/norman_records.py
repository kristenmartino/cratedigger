"""Norman Records scraper.

Norman Records (normanrecords.com) is a UK independent record shop. Like
Boomkat they don't publish a clean RSS feed for their weekly arrivals, so we
scrape the new-this-week listing and parse product cards. List page only —
detail pages would N+1 fan-out and bust our 1 req/sec budget.

Same defensive shape as the Boomkat scraper: a tuple of selector candidates
(Norman has rebuilt its catalog template a few times) and `_first_text` /
`_first_attr` helpers that take the first non-empty hit. If the site
restructures, this scraper degrades to an empty list rather than crashing.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser, Node

from agent.config import settings
from agent.sources._debug import log_no_cards_diagnostic
from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.norman_records")

BASE_URL = "https://www.normanrecords.com"
INDEX_PATH = "/en/this-week-only"

_CARD_SELECTORS = (
    "div.product-card",
    "li.product-card",
    "article.product",
    "div.product-tile",
    "div.product-listing",
    "div[data-product-id]",
    ".product-item",
)


def _first_text(node: Node, *selectors: str) -> str:
    for sel in selectors:
        el = node.css_first(sel)
        if el is not None:
            text = el.text(deep=True, separator=" ", strip=True)
            if text:
                return text
    return ""


def _first_attr(node: Node, attr: str, *selectors: str) -> str | None:
    for sel in selectors:
        el = node.css_first(sel)
        if el is not None:
            val = el.attributes.get(attr)
            if val:
                return val
    return None


def _parse_card(card: Node) -> RawRelease | None:
    artist = _first_text(
        card, ".artist", ".product-artist", "[itemprop=brand]", "h3 a", "h3", ".artist-name"
    )
    title = _first_text(
        card, ".title", ".product-title", "[itemprop=name]", "h2 a", "h2", ".release-title"
    )
    label = _first_text(card, ".label", ".product-label", "[data-label]", ".record-label")
    cover = _first_attr(card, "src", "img.cover", "img[itemprop=image]", "img")
    href = _first_attr(card, "href", "a.product-link", "a[itemprop=url]", "h3 a", "h2 a", "a")
    blurb = _first_text(card, ".description", ".blurb", ".product-description", ".review", "p")

    if not artist and not title:
        return None

    # Norman occasionally renders "Artist - Title" in a single field.
    if not artist and title:
        for sep in (" — ", " - ", " – "):
            if sep in title:
                head, _, tail = title.partition(sep)
                artist, title = head.strip(), tail.strip()
                break

    return RawRelease(
        title=title,
        artist=artist,
        label=label or None,
        catalog_number=None,
        release_date=None,
        url=urljoin(BASE_URL, href) if href else BASE_URL + INDEX_PATH,
        cover_art_url=cover,
        description=blurb or f"{artist} — {title}".strip(" —"),
        source_slug="norman-records",
    )


async def scrape_norman_records() -> list[RawRelease]:
    """Scrape Norman Records' this-week-only page. Returns RawReleases."""
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as http:
            resp = await http.get(BASE_URL + INDEX_PATH)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error("Norman Records fetch failed: %s", e)
        return []

    tree = HTMLParser(html)
    cards: list[Node] = []
    for sel in _CARD_SELECTORS:
        cards = tree.css(sel)
        if cards:
            logger.info("Norman Records: %d cards via selector %r", len(cards), sel)
            break

    if not cards:
        log_no_cards_diagnostic(logger, "norman-records", html)
        return []

    out: list[RawRelease] = []
    for card in cards:
        try:
            release = _parse_card(card)
        except Exception as e:
            logger.warning("Norman Records card parse error: %s", e)
            continue
        if release is not None:
            out.append(release)

    logger.info("Norman Records: parsed %d releases", len(out))
    return out
