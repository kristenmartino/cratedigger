"""Resident Advisor scraper.

RA's reviews live at ra.co/reviews. They render the page through Next.js, so
the cleanest extraction path is the embedded `__NEXT_DATA__` JSON blob —
avoids the brittle DOM-selector chase against a hydrated SPA. Falls back to
DOM selectors if the JSON shape changes.

Respect robots.txt; 1 req/sec; real User-Agent. List page only.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession
from agent.sources.rss import RawRelease

logger = logging.getLogger("cratedigger-agent.sources.resident_advisor")

BASE_URL = "https://ra.co"
INDEX_PATH = "/reviews"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _walk(obj, key: str):
    """Yield every value matching `key` anywhere in a nested JSON tree."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, key)


def _from_next_data(html: str) -> list[RawRelease]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning("RA __NEXT_DATA__ decode failed: %s", e)
        return []

    out: list[RawRelease] = []
    seen_ids: set[str] = set()

    # Apollo cache shape: typenames like "Review" carry the fields we want.
    for value in _walk(data, "__typename"):
        pass  # consume; the dict we want is the parent

    # Easier approach: walk every dict and pick ones that look like a review.
    def _scan(node):
        if isinstance(node, dict):
            tn = node.get("__typename") or node.get("type")
            if tn in {"Review", "ReviewListItem"} or (
                "title" in node and "artist" in node and "contentUrl" in node
            ):
                rid = node.get("id") or node.get("contentUrl") or node.get("title")
                if rid and rid in seen_ids:
                    return
                if rid:
                    seen_ids.add(rid)
                title = (node.get("title") or "").strip()
                artist_raw = node.get("artist") or node.get("artists") or ""
                if isinstance(artist_raw, list):
                    artist = ", ".join(
                        a.get("name", "") if isinstance(a, dict) else str(a)
                        for a in artist_raw
                    ).strip()
                elif isinstance(artist_raw, dict):
                    artist = artist_raw.get("name", "").strip()
                else:
                    artist = str(artist_raw).strip()
                href = node.get("contentUrl") or node.get("url")
                cover = (
                    (node.get("image") or {}).get("url")
                    if isinstance(node.get("image"), dict)
                    else node.get("imageUrl") or node.get("image")
                )
                blurb = (node.get("blurb") or node.get("standfirst") or "").strip()
                label = (node.get("label") or "").strip() or None
                if title or artist:
                    out.append(RawRelease(
                        title=title,
                        artist=artist,
                        label=label,
                        catalog_number=None,
                        release_date=None,
                        url=urljoin(BASE_URL, href) if href else BASE_URL + INDEX_PATH,
                        cover_art_url=cover,
                        description=blurb or f"{artist} — {title}".strip(" —"),
                        source_slug="resident-advisor",
                    ))
            for v in node.values():
                _scan(v)
        elif isinstance(node, list):
            for item in node:
                _scan(item)

    _scan(data)
    return out


def _from_dom(html: str) -> list[RawRelease]:
    """DOM fallback if __NEXT_DATA__ is missing or shape changes."""
    tree = HTMLParser(html)
    cards: list[Node] = (
        tree.css("a[href^='/reviews/']")
        or tree.css("article")
    )
    out: list[RawRelease] = []
    for card in cards:
        href = card.attributes.get("href") if card.tag == "a" else None
        if href is None:
            link = card.css_first("a[href^='/reviews/']")
            if link:
                href = link.attributes.get("href")
        title_el = card.css_first("h2, h3, .title")
        artist_el = card.css_first(".artist, .review-artist")
        img_el = card.css_first("img")
        title = title_el.text(strip=True) if title_el else ""
        artist = artist_el.text(strip=True) if artist_el else ""
        if not (title or artist):
            continue
        out.append(RawRelease(
            title=title,
            artist=artist,
            label=None,
            catalog_number=None,
            release_date=None,
            url=urljoin(BASE_URL, href) if href else BASE_URL + INDEX_PATH,
            cover_art_url=img_el.attributes.get("src") if img_el else None,
            description=f"{artist} — {title}".strip(" —"),
            source_slug="resident-advisor",
        ))
    return out


async def scrape_resident_advisor() -> list[RawRelease]:
    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with AsyncSession(
            timeout=20.0, headers=headers, impersonate=IMPERSONATE
        ) as http:
            resp = await http.get(BASE_URL + INDEX_PATH, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error("RA fetch failed: %s", e)
        return []

    out = _from_next_data(html)
    if not out:
        out = _from_dom(html)
        if out:
            logger.info("RA: %d reviews via DOM fallback", len(out))
    else:
        logger.info("RA: %d reviews via __NEXT_DATA__", len(out))

    if not out:
        logger.warning("RA: no reviews found — page structure may have changed")

    return out
