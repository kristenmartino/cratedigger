"""Metadata enrichment via MusicBrainz + Discogs.

Most ingested releases come out of crawl with only `(artist, title, url)`
populated — the URL points at the source's RSS entry or shop product page,
not at Bandcamp/Spotify, and cover art is sparse because few RSS feeds
include `media:thumbnail`. Without enrichment, the email's "Listen ↗"
button lands on an article page and most records have no cover.

This module looks up each `(artist, title)` against open music metadata
APIs and returns whatever it finds: a cover art URL, a Bandcamp URL, a
Spotify URL. Three nulls is a valid result.

MusicBrainz is primary because:
  - free, no auth, generous rate limit (1 req/sec per User-Agent)
  - explicit URL relationships (Bandcamp, Spotify, Apple Music, etc.)
  - paired with Cover Art Archive (deterministic image URLs by MBID)

Discogs is an optional fallback for cover art when MB has no image. Set
DISCOGS_TOKEN to enable. Without it, the Discogs path skips cleanly.
We don't ask Discogs for Bandcamp URLs — Discogs doesn't store them.

Rate limit policy:
  - MusicBrainz: 1 req/sec PER USER-AGENT, hard. Enforced here by a
    module-level lock + min-interval, NOT per-task. Concurrent callers
    share the same throttle.
  - Discogs: 60/min auth'd = 1 req/sec equivalent. Same throttle pattern
    via separate lock so a slow MB call doesn't block Discogs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession

logger = logging.getLogger("cratedigger-agent.metadata")

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"
DISCOGS_BASE = "https://api.discogs.com"

# Per MusicBrainz policy. We're polite-bot citizens; the rest of the
# pipeline is also serialized via this lock to keep the total RPS in budget.
_MB_LOCK = asyncio.Lock()
_MB_LAST_AT = 0.0
_MB_MIN_INTERVAL = 1.05  # tiny headroom over the 1 sec policy floor

_DISCOGS_LOCK = asyncio.Lock()
_DISCOGS_LAST_AT = 0.0
_DISCOGS_MIN_INTERVAL = 1.05


async def _throttle(lock: asyncio.Lock, last_at_attr: str, min_interval: float) -> None:
    """Sleep enough so the next request honors the min-interval since the
    last one in the same channel."""
    g = globals()
    async with lock:
        now = time.monotonic()
        wait = max(0.0, min_interval - (now - g[last_at_attr]))
        if wait > 0:
            await asyncio.sleep(wait)
        g[last_at_attr] = time.monotonic()


# ── MusicBrainz ─────────────────────────────────────────────────────────


async def _mb_search_release_group(
    http: AsyncSession, artist: str, title: str
) -> str | None:
    """Search MB for a release-group matching (artist, title). Return MBID."""
    await _throttle(_MB_LOCK, "_MB_LAST_AT", _MB_MIN_INTERVAL)
    query = f'releasegroup:"{title}" AND artist:"{artist}"'
    try:
        resp = await http.get(
            f"{MB_BASE}/release-group/",
            params={"query": query, "fmt": "json", "limit": "1"},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            logger.info(
                "MB search %r — %r returned %d", artist, title, resp.status_code
            )
            return None
        data = resp.json()
    except Exception as e:
        logger.warning("MB search %r — %r raised: %s", artist, title, e)
        return None

    rgs = data.get("release-groups") or []
    if not rgs:
        return None
    return rgs[0].get("id")


async def _mb_lookup_release_group(
    http: AsyncSession, mbid: str
) -> dict[str, Any] | None:
    """Fetch full release-group with URL relationships + cover-art-archive count."""
    await _throttle(_MB_LOCK, "_MB_LAST_AT", _MB_MIN_INTERVAL)
    try:
        resp = await http.get(
            f"{MB_BASE}/release-group/{mbid}",
            params={"inc": "url-rels", "fmt": "json"},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as e:
        logger.warning("MB lookup %s raised: %s", mbid, e)
        return None


def _parse_mb_urls(rg_data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pull (bandcamp_url, spotify_url) from MB release-group's url-rels."""
    bandcamp_url: str | None = None
    spotify_url: str | None = None
    for rel in rg_data.get("relations") or []:
        url = (rel.get("url") or {}).get("resource") or ""
        if not url:
            continue
        if "bandcamp.com" in url and bandcamp_url is None:
            bandcamp_url = url
        elif "open.spotify.com" in url and spotify_url is None:
            spotify_url = url
    return bandcamp_url, spotify_url


def _mb_cover_url(mbid: str, rg_data: dict[str, Any]) -> str | None:
    """If MB says cover-art-archive has at least one image for this MBID,
    construct the deterministic CAA URL. No extra HTTP call needed."""
    caa = rg_data.get("cover-art-archive") or {}
    if int(caa.get("count") or 0) > 0:
        return f"{CAA_BASE}/release-group/{mbid}/front-500"
    return None


# ── Discogs (fallback for cover art only) ──────────────────────────────


async def _discogs_cover(
    http: AsyncSession, artist: str, title: str
) -> str | None:
    """Best-effort cover-image lookup via Discogs Search. Skips silently
    without a token; returns None on miss or any error."""
    token = settings.discogs_token
    if not token:
        return None
    await _throttle(_DISCOGS_LOCK, "_DISCOGS_LAST_AT", _DISCOGS_MIN_INTERVAL)
    try:
        resp = await http.get(
            f"{DISCOGS_BASE}/database/search",
            params={
                "q": f"{artist} {title}",
                "type": "release",
                "per_page": "1",
            },
            headers={
                "Authorization": f"Discogs token={token}",
                "User-Agent": settings.crawler_user_agent,
            },
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as e:
        logger.warning("Discogs lookup %r — %r raised: %s", artist, title, e)
        return None
    results = data.get("results") or []
    if not results:
        return None
    cover = results[0].get("cover_image")
    if cover and not cover.startswith("http"):
        return None
    return cover


# ── Public API ──────────────────────────────────────────────────────────


async def lookup_release(artist: str, title: str) -> dict[str, str | None]:
    """Return whatever metadata we can find for (artist, title).

    Schema:
        {"cover_art_url": str|None, "bandcamp_url": str|None,
         "spotify_url": str|None, "mbid": str|None}

    All fields may be None — that's a valid result for releases the public
    metadata layer doesn't index (small-label drops, mailing-list releases,
    super-fresh records that haven't propagated yet).
    """
    result: dict[str, str | None] = {
        "cover_art_url": None,
        "bandcamp_url": None,
        "spotify_url": None,
        "mbid": None,
    }
    if not (artist and title):
        return result

    headers = {
        "User-Agent": settings.crawler_user_agent,
        "Accept": "application/json",
    }

    try:
        async with AsyncSession(
            timeout=15.0, headers=headers, impersonate=IMPERSONATE
        ) as http:
            mbid = await _mb_search_release_group(http, artist, title)
            if mbid:
                result["mbid"] = mbid
                rg = await _mb_lookup_release_group(http, mbid)
                if rg:
                    result["cover_art_url"] = _mb_cover_url(mbid, rg)
                    bc, sp = _parse_mb_urls(rg)
                    result["bandcamp_url"] = bc
                    result["spotify_url"] = sp

            # Discogs fallback — only when MB didn't find a cover, to save
            # quota. The user gave us a Discogs token only as a fill-in.
            if not result["cover_art_url"]:
                result["cover_art_url"] = await _discogs_cover(http, artist, title)
    except Exception as e:
        # Top-level catch so an enrichment failure can't crash the
        # workflow. Empty result is a valid response from this function.
        logger.error("metadata lookup_release %r — %r raised: %s", artist, title, e)

    return result
