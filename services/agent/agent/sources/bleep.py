"""Bleep scraper.

Bleep (bleep.com) is the Warp-affiliated UK shop, broad electronic and
experimental coverage. Modern e-commerce template close in shape to Boomkat
and Norman Records, so the scraper is a near-mirror of those.

Same conventions: 1 req/sec budget, real User-Agent, list page only, fail-soft
on DOM drift.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser, Node

from agent.config import settings
from agent.sources._debug import log_no_cards_diagnostic
from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.bleep")

BASE_URL = "https://bleep.com"
INDEX_PATH = "/genre/all/new-releases"

_CARD_SELECTORS = (
    "div.product-card",
    "li.product-card",
    "article.product",
    "div.release-card",
    "div.product-tile",
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
        source_slug="bleep",
    )


async def scrape_bleep() -> list[RawRelease]:
    """Scrape Bleep's new-releases listing. Returns RawReleases."""
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True
        ) as http:
            resp = await http.get(BASE_URL + INDEX_PATH)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error("Bleep fetch failed: %s", e)
        return []

    tree = HTMLParser(html)
    cards: list[Node] = []
    for sel in _CARD_SELECTORS:
        cards = tree.css(sel)
        if cards:
            logger.info("Bleep: %d cards via selector %r", len(cards), sel)
            break

    if not cards:
        log_no_cards_diagnostic(logger, "bleep", html)
        return []

    out: list[RawRelease] = []
    for card in cards:
        try:
            release = _parse_card(card)
        except Exception as e:
            logger.warning("Bleep card parse error: %s", e)
            continue
        if release is not None:
            out.append(release)

    logger.info("Bleep: parsed %d releases", len(out))
    return out
