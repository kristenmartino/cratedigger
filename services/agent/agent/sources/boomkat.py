"""Boomkat scraper.

Boomkat doesn't publish a clean RSS feed (per SPEC.md §3.2). We scrape
boomkat.com/new-this-week. Be respectful: 1 req/sec rate limit, real
User-Agent, robots.txt-respecting.

Boomkat's HTML structure changes occasionally; expect this scraper to break
and need maintenance. We try multiple selector candidates and fail-soft.
List page only — we do NOT follow detail pages in this version (avoids the
N+1 fan-out and the rate-limit window required to scrape ~50 detail pages).
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from agent.config import settings
from agent.sources._debug import log_no_cards_diagnostic
from agent.sources._http import IMPERSONATE, AsyncSession
from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.boomkat")

BASE_URL = "https://boomkat.com"
INDEX_PATH = "/new-this-week"

# Tried in order. Boomkat's class names drift; the first non-empty hit wins.
_CARD_SELECTORS = (
    "div.product-card",
    "li.product-card",
    "article.product-card",
    "div.product-list-item",
    "li.product-list-item",
    "div[data-product-id]",
    ".product",
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
    artist = _first_text(card, ".artist", ".product-artist", "[itemprop=brand]", "h3 a", "h3")
    title = _first_text(card, ".title", ".product-title", "[itemprop=name]", "h2 a", "h2")
    label = _first_text(card, ".label", ".product-label", "[data-label]")
    cover = _first_attr(card, "src", "img.cover", "img[itemprop=image]", "img")
    href = _first_attr(card, "href", "a.product-link", "a[itemprop=url]", "h3 a", "h2 a", "a")
    blurb = _first_text(card, ".description", ".blurb", ".product-description", "p")

    if not artist and not title:
        return None

    # Boomkat sometimes renders title as "Artist — Title" in a single field.
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
        source_slug="boomkat",
    )


async def scrape_boomkat() -> list[RawRelease]:
    """Scrape Boomkat's new-this-week page. Returns RawReleases."""
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with AsyncSession(
            timeout=20.0, headers=headers, impersonate=IMPERSONATE
        ) as http:
            resp = await http.get(BASE_URL + INDEX_PATH, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error("Boomkat fetch failed: %s", e)
        return []

    tree = HTMLParser(html)
    cards: list[Node] = []
    for sel in _CARD_SELECTORS:
        cards = tree.css(sel)
        if cards:
            logger.info("Boomkat: %d cards via selector %r", len(cards), sel)
            break

    if not cards:
        log_no_cards_diagnostic(logger, "boomkat", html)
        return []

    out: list[RawRelease] = []
    for card in cards:
        try:
            release = _parse_card(card)
        except Exception as e:
            logger.warning("Boomkat card parse error: %s", e)
            continue
        if release is not None:
            out.append(release)

    logger.info("Boomkat: parsed %d releases", len(out))
    return out
