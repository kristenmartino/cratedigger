"""RSS source crawler. Pattern adapted from sift-api/services/rss.py.

Differences from Sift:
  - The FEEDS list is GONE. Sources live in the `sources` DB table
    (per SPEC.md §3.1) — adding a 6th source is an INSERT, not a code change.
  - Normalization for dedup is `(artist, title)` not `(source_url, content_hash)`.
  - Image extraction is preserved (Bandcamp/label cover-art URLs come back
    in the feed; the metadata fetcher handles fallback).

This is a skeleton. Boomkat and Resident Advisor get their own scrape modules
since they don't publish clean RSS.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

from agent.config import settings

logger = logging.getLogger("cratedigger-agent.sources.rss")


@dataclass
class RawRelease:
    """Pre-deduplication shape coming out of an RSS/scrape pass."""
    title: str
    artist: str
    label: str | None
    catalog_number: str | None
    release_date: datetime | None
    url: str
    cover_art_url: str | None
    description: str
    source_slug: str  # "boomkat" | "quietus" | ...


# ── Normalization for dedup ──────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, ASCII-fold, strip punctuation, collapse whitespace.

    Punctuation is stripped entirely (replaced with empty), not with space —
    otherwise "Nous'klaer" and "Nousklaer" produce different dedup keys.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SPACE_RE.sub(" ", _PUNCT_RE.sub("", folded.lower())).strip()


def stable_hash(s: str) -> str:
    """Stable content hash for change detection. Used on description bodies
    to detect when a source has rewritten an existing release entry."""
    return hashlib.blake2b(s.encode("utf-8"), digest_size=12).hexdigest()


# ── Title → (artist, release_title) heuristic ────────────────────────────
#
# Each magazine RSS feed encodes (artist, title) differently in the entry
# headline. Live samples observed in the first crawl runs:
#
#   A Closer Listen      "SHHE ~ THALASSA"                    → ~
#   Aquarium Drunkard    "Setting :: S/T"                     → ::
#   Bandcamp Daily       'Aldous Harding, "Train On The Island"'   → comma + quotes
#   Boomkat / RA / etc.  "Artist — Title" / "Artist - Title"  → em-dash / hyphen
#
# News-headline feeds (Pitchfork, Stereogum, FACT, Crack Magazine) don't
# follow any of these — articles like "Spellbound Festival announces 2026
# programme" aren't release-shaped at all. For those, parse_release_title
# returns ("", title) and the downstream pipeline decides what to do
# (extraction via LLM is a separate workstream).
#
# Order matters: the BD quoted-title pattern is checked before the comma-
# split so that headlines containing an incidental comma ("…on Bandcamp,
# April 2026") aren't mis-split into a fake artist.

_SEPARATORS: tuple[str, ...] = (" — ", " – ", " ~ ", " :: ", " - ")

_QUOTE_OPEN = "“"   # left double curly quote
_QUOTE_CLOSE = "”"  # right double curly quote
_QUOTED_TITLE_RE = re.compile(
    rf'^(?P<artist>.+?),\s+["{_QUOTE_OPEN}](?P<title>[^"{_QUOTE_CLOSE}]+)["{_QUOTE_CLOSE}]\s*$'
)


def parse_release_title(title: str) -> tuple[str, str]:
    """Split a feed entry title into (artist, release_title).

    Returns ("", title) if no recognized release pattern matches — the entry
    is probably a news headline or feature article, not a release line.
    """
    if not title:
        return "", title

    m = _QUOTED_TITLE_RE.match(title)
    if m:
        return m["artist"].strip(), m["title"].strip()

    for sep in _SEPARATORS:
        if sep in title:
            head, _, tail = title.partition(sep)
            return head.strip(), tail.strip()

    return "", title


# ── RSS fetcher ──────────────────────────────────────────────────────────

async def fetch_rss(source_slug: str, url: str) -> list[RawRelease]:
    """Fetch and parse one RSS feed. Returns RawReleases."""
    headers = {
        "User-Agent": settings.crawler_user_agent,
        "Accept": (
            "application/rss+xml, application/atom+xml;q=0.9, "
            "application/xml;q=0.8, text/xml;q=0.7, */*;q=0.5"
        ),
    }
    try:
        async with httpx.AsyncClient(
            timeout=20.0, headers=headers, follow_redirects=True
        ) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            body = resp.content
    except Exception as e:
        logger.error("RSS fetch %s (%s) failed: %s", source_slug, url, e)
        return []

    parsed = await asyncio.to_thread(feedparser.parse, body)
    out: list[RawRelease] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        release_artist, release_title = parse_release_title(title)

        link = entry.get("link") or ""
        description = (entry.get("summary") or entry.get("description") or "").strip()

        # Try to extract a cover image
        cover = None
        for media_key in ("media_thumbnail", "media_content"):
            media = entry.get(media_key)
            if media:
                cover = media[0].get("url") if isinstance(media, list) else media.get("url")
                if cover:
                    break

        published = entry.get("published_parsed") or entry.get("updated_parsed")
        release_date = (
            datetime(*published[:6], tzinfo=timezone.utc) if published else None
        )

        out.append(RawRelease(
            title=release_title,
            artist=release_artist,
            label=None,
            catalog_number=None,
            release_date=release_date,
            url=link,
            cover_art_url=cover,
            description=description,
            source_slug=source_slug,
        ))

    logger.info("RSS %s: %d entries", source_slug, len(out))
    return out


async def fetch_all_rss_sources(sources: list[dict]) -> list[RawRelease]:
    """Fan out across active RSS-method sources in parallel."""
    rss_sources = [s for s in sources if s["ingest_method"] == "rss" and s["active"]]
    if not rss_sources:
        return []

    tasks = [fetch_rss(s["slug"], s["ingest_url"]) for s in rss_sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    flat: list[RawRelease] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("RSS source raised: %s", r)
            continue
        flat.extend(r)
    return flat
