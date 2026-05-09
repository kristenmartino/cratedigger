"""Hardwax scraper.

Hardwax (hardwax.com) is the Berlin techno / dub-techno reference shop. Their
HTML is famously sparse and text-heavy — historically a `<dl>` of releases
where each `<dt>` is artist/title and the following `<dd>` is the editorial
blurb. The current template has moved closer to a card grid, but several of
the legacy CSS hooks survive. This scraper tries the modern card selectors
first, then falls back to the legacy `<dl>` shape.

Same conventions as the Boomkat / Norman Records scrapers: 1 req/sec budget,
real User-Agent, list page only, fail-soft on DOM drift.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser, Node

from agent.config import settings
from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.hardwax")

BASE_URL = "https://hardwax.com"
INDEX_PATH = "/this-week/"

_CARD_SELECTORS = (
    "li.record",
    "div.record",
    "article.record",
    "div.product",
    "li.product",
    ".release-item",
    "dl.records > dt",  # legacy <dt>/<dd> pair — handled specially below
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
    artist = _first_text(card, ".artist", ".record-artist", "h3 a", "h3", ".name")
    title = _first_text(card, ".title", ".record-title", "h2 a", "h2", ".release")
    label = _first_text(card, ".label", ".record-label", ".labelname")
    cover = _first_attr(card, "src", "img.cover", "img[itemprop=image]", "img")
    href = _first_attr(card, "href", "a.record-link", "a[itemprop=url]", "h3 a", "h2 a", "a")
    blurb = _first_text(card, ".description", ".comment", ".review", "p")
    catno = _first_text(card, ".catno", ".catalog", ".cat-number")

    if not artist and not title:
        # Hardwax legacy: artist and title may be combined in the card text as
        # "Artist - Title (Label CAT001)". Try the whole-card text as a fallback.
        whole = card.text(deep=True, separator=" ", strip=True)
        for sep in (" — ", " - ", " – "):
            if sep in whole:
                head, _, tail = whole.partition(sep)
                artist = artist or head.strip()
                title = title or tail.split("(")[0].strip()
                break

    if not artist and not title:
        return None

    return RawRelease(
        title=title,
        artist=artist,
        label=label or None,
        catalog_number=catno or None,
        release_date=None,
        url=urljoin(BASE_URL, href) if href else BASE_URL + INDEX_PATH,
        cover_art_url=cover,
        description=blurb or f"{artist} — {title}".strip(" —"),
        source_slug="hardwax",
    )


async def scrape_hardwax() -> list[RawRelease]:
    """Scrape Hardwax's this-week listing. Returns RawReleases."""
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True
        ) as http:
            resp = await http.get(BASE_URL + INDEX_PATH)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error("Hardwax fetch failed: %s", e)
        return []

    tree = HTMLParser(html)
    cards: list[Node] = []
    for sel in _CARD_SELECTORS:
        cards = tree.css(sel)
        if cards:
            logger.info("Hardwax: %d cards via selector %r", len(cards), sel)
            break

    if not cards:
        logger.warning("Hardwax: no records found — selectors may have drifted")
        return []

    out: list[RawRelease] = []
    for card in cards:
        try:
            release = _parse_card(card)
        except Exception as e:
            logger.warning("Hardwax card parse error: %s", e)
            continue
        if release is not None:
            out.append(release)

    logger.info("Hardwax: parsed %d releases", len(out))
    return out
