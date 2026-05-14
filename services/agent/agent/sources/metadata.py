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

Artist verification (added after a Dollar Diamonds release got matched
to "Street Corner Symphonies Volume 12: 1960" because the title term
"Volume Five" was generic and the artist wasn't indexed in MB):
the lookup confirms the returned release's `artist-credit` loosely
matches the input artist. Mismatch → reject. No-cover beats wrong-cover.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import unicodedata
from typing import Any

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession

logger = logging.getLogger("cratedigger-agent.metadata")

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"
DISCOGS_BASE = "https://api.discogs.com"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# Per MusicBrainz policy. We're polite-bot citizens; the rest of the
# pipeline is also serialized via this lock to keep the total RPS in budget.
_MB_LOCK = asyncio.Lock()
_MB_LAST_AT = 0.0
_MB_MIN_INTERVAL = 1.05  # tiny headroom over the 1 sec policy floor

_DISCOGS_LOCK = asyncio.Lock()
_DISCOGS_LAST_AT = 0.0
_DISCOGS_MIN_INTERVAL = 1.05

# Spotify Web API: documented limits aren't published as a fixed number, but
# in practice 180 req/min is the soft ceiling for Client Credentials. We
# pace at ~10 req/sec to be safe and still finish a full crawl in seconds.
_SPOTIFY_LOCK = asyncio.Lock()
_SPOTIFY_LAST_AT = 0.0
_SPOTIFY_MIN_INTERVAL = 0.1

# Spotify access token cache (Client Credentials flow). One token per
# pipeline run; valid for ~1 hour. Module-level so concurrent lookups
# share the same token instead of each fetching their own.
_SPOTIFY_TOKEN: dict[str, Any] | None = None
_SPOTIFY_TOKEN_LOCK = asyncio.Lock()


# ── Artist-match verification ───────────────────────────────────────────


_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _normalize_artist(name: str) -> str:
    """Lowercase, ASCII-fold, drop punctuation, collapse whitespace.

    Loose enough that 'Mary Yuzovskaya' == 'mary yuzovskaya', that 'Burial'
    matches 'BURIAL', and that 'Boards Of Canada' matches 'Boards of Canada'.
    Strict enough that 'Dollar Diamonds' never matches 'Various Artists'.
    """
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return _WS_RE.sub(" ", _PUNCT_RE.sub("", folded.lower())).strip()


# 4-char min on the shorter side keeps "the", "a", "an", "and", "of"
# from anchoring false-positive substring matches like "the" matching
# "the beatles". Real artist names this short ("M83", "U2") match on
# exact-equality, never substring, so the 4-char cost is bounded.
_MIN_SUBSTRING_LEN = 4


def _artists_match(input_artist: str, candidate_artist: str) -> bool:
    """Strict artist-equivalence check. No fuzzy thresholds.

    Two acceptance rules, in order:
      1. Normalized exact match.
      2. One is a substring of the other AND the shorter side is at
         least 3 chars. Handles label-prefix variants like 'Various
         Artists - Hyperdub' matching 'Hyperdub', and single-name
         artists against feature credits like 'Burial' matching
         'Burial Four Tet' (after punctuation strip).
         The 3-char minimum guards against single-letter false
         positives like 'M' matching 'M Lamar'.

    Anything else → reject. We don't do token-overlap, Levenshtein,
    Jaccard, or rapidfuzz thresholds here. Those approaches all need
    calibration data we don't have yet — picking thresholds by feel
    is how Dollar Diamonds got matched to Street Corner Symphonies.

    Coverage cost of strict-only:
      - Order variations ('The Cinematic Orchestra' vs
        'Cinematic Orchestra, The') will miss
      - Feature credits that aren't substring-contained will miss
        (e.g., input 'James Blake' vs MB's 'James Blake & friends'
        IS caught by substring; input 'Burial / Four Tet' vs MB's
        'Burial' is also caught; but 'A & B' vs 'B & A' is not)
      - Aliases entirely out of scope

    Coverage misses produce "no cover" — visible but recoverable.
    The fuzzier alternatives would produce "wrong cover" instead —
    invisible to the pipeline, visible to the reader, and corrosive
    to editorial trust. Bias is intentional.

    To revisit this rule: instrument every rejection in production
    for a week, hand-label the (input, candidate) pairs, then decide
    if a fuzzy threshold buys real coverage without false positives.
    """
    a = _normalize_artist(input_artist)
    b = _normalize_artist(candidate_artist)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < _MIN_SUBSTRING_LEN:
        return False
    # Whole-word substring containment. Pad both sides with spaces so
    # "burial" doesn't match "burialground" but DOES match anywhere
    # inside "burial four tet" — including at the start or end.
    return f" {shorter} " in f" {longer} "


def _mb_artist_credit_name(rg: dict[str, Any]) -> str:
    """Read the artist-credit's display name out of an MB release-group.

    MB returns artist-credit as a list of {name, joinphrase, artist:{...}}
    entries. Concatenated, they form the display string ('Burial &
    Four Tet'). For single-artist releases the list has one entry.
    """
    credits = rg.get("artist-credit") or []
    parts: list[str] = []
    for ac in credits:
        name = ac.get("name") or (ac.get("artist") or {}).get("name") or ""
        parts.append(name)
        join = ac.get("joinphrase") or ""
        if join:
            parts.append(join)
    return "".join(parts).strip()


# ── Rate-limit throttles ────────────────────────────────────────────────


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
    """Search MB for a release-group matching (artist, title). Return MBID
    ONLY when the top result's artist-credit verifies against the input.

    Top-result-without-verification was how a Dollar Diamonds release got
    matched to a doo-wop compilation: Lucene's AND degrades to OR-like
    scoring when one side has no matches, so a generic title like
    "Volume Five" wins on its own. We pull artist-credit back in the
    search response (with `inc=artist-credits` via the docs note below)
    and post-filter — no match, no MBID.
    """
    await _throttle(_MB_LOCK, "_MB_LAST_AT", _MB_MIN_INTERVAL)
    # Lucene-y: AND filter both sides, request a few results so we can
    # pick the first that ALSO verifies on artist-credit (rather than
    # blindly trusting top-1).
    query = f'releasegroup:"{title}" AND artist:"{artist}"'
    try:
        resp = await http.get(
            f"{MB_BASE}/release-group/",
            params={"query": query, "fmt": "json", "limit": "5"},
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
    for rg in rgs:
        matched_artist = _mb_artist_credit_name(rg)
        if _artists_match(artist, matched_artist):
            return rg.get("id")
        logger.info(
            "MB candidate rejected (artist mismatch): input %r vs matched %r",
            artist, matched_artist,
        )
    return None


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
    without a token; returns None on miss or any error.

    Uses Discogs's STRUCTURED search params (`artist=…&release_title=…`)
    rather than full-text `q=` so the upstream match is artist-aware.
    Post-filter still applies: Discogs's results[].title is shaped like
    'Artist - Title' so we parse the artist half and verify with
    `_artists_match`, same threshold the MB path uses.
    """
    token = settings.discogs_token
    if not token:
        return None
    await _throttle(_DISCOGS_LOCK, "_DISCOGS_LAST_AT", _DISCOGS_MIN_INTERVAL)
    try:
        resp = await http.get(
            f"{DISCOGS_BASE}/database/search",
            params={
                "artist": artist,
                "release_title": title,
                "type": "release",
                "per_page": "5",
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

    for item in data.get("results") or []:
        # Discogs result titles are "Artist - Title". Pull the artist
        # half and verify against input. Reject 'Various Artists' and
        # other generic-comp matches.
        item_title = item.get("title") or ""
        candidate_artist = item_title.split(" - ", 1)[0] if " - " in item_title else ""
        if not _artists_match(artist, candidate_artist):
            logger.info(
                "Discogs candidate rejected (artist mismatch): "
                "input %r vs matched %r (full title: %r)",
                artist, candidate_artist, item_title,
            )
            continue
        cover = item.get("cover_image")
        if cover and isinstance(cover, str) and cover.startswith("http"):
            return cover
    return None


# ── Spotify (Client Credentials — public catalog search only) ──────────
#
# MusicBrainz's URL-relationships table is volunteer-contributed and only
# covers a small fraction of records' Spotify URLs. Most releases score
# zero Spotify links from MB even when they're on Spotify.
#
# This adds a direct Spotify catalog search as a fallback. Uses the
# Client Credentials grant — server-to-server, no user OAuth required.
# That separate Spotify OAuth flow (for writing user playlists) is a
# different workstream; the catalog search is public and free.
#
# Same artist-match verification as MB: Spotify search returns top
# results sorted by relevance, but a generic title can pull in the wrong
# artist. Reject results where the artists don't match.


async def _spotify_token(http: AsyncSession) -> str | None:
    """Return a cached Client Credentials access token, fetching if needed.

    Tokens are 1-hour TTL; we cache module-level and refresh ~60s before
    expiry. Concurrent callers share the same token via the lock.
    """
    global _SPOTIFY_TOKEN
    if not (settings.spotify_client_id and settings.spotify_client_secret):
        return None

    async with _SPOTIFY_TOKEN_LOCK:
        now = time.monotonic()
        if _SPOTIFY_TOKEN and _SPOTIFY_TOKEN.get("expires_at", 0) > now:
            return _SPOTIFY_TOKEN["access_token"]

        # Client Credentials grant: HTTP Basic header with
        # base64(client_id:client_secret) + grant_type=client_credentials.
        creds = base64.b64encode(
            f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
        ).decode()
        try:
            resp = await http.post(
                SPOTIFY_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                allow_redirects=True,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Spotify token request returned %d: %s",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
        except Exception as e:
            logger.warning("Spotify token fetch raised: %s", e)
            return None

        token = data.get("access_token")
        expires_in = int(data.get("expires_in") or 3600)
        if not token:
            return None
        _SPOTIFY_TOKEN = {
            "access_token": token,
            "expires_at": now + expires_in - 60,  # 60s buffer
        }
        return token


async def _spotify_search_album(
    http: AsyncSession, artist: str, title: str
) -> str | None:
    """Search Spotify's catalog for an album matching (artist, title).

    Returns the album's spotify.com URL if a verified match is found,
    None otherwise. Verification is the same `_artists_match` rule the MB
    path uses — Spotify's structured `artist:"X" album:"Y"` query is more
    precise than free-text search but can still surface wrong results
    when the artist is obscure or the title is generic.
    """
    token = await _spotify_token(http)
    if not token:
        return None

    await _throttle(_SPOTIFY_LOCK, "_SPOTIFY_LAST_AT", _SPOTIFY_MIN_INTERVAL)
    try:
        resp = await http.get(
            f"{SPOTIFY_API_BASE}/search",
            params={
                "q": f'artist:"{artist}" album:"{title}"',
                "type": "album",
                "limit": "5",
            },
            headers={"Authorization": f"Bearer {token}"},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            logger.info(
                "Spotify search %r — %r returned %d",
                artist, title, resp.status_code,
            )
            return None
        data = resp.json()
    except Exception as e:
        logger.warning("Spotify search %r — %r raised: %s", artist, title, e)
        return None

    items = (data.get("albums") or {}).get("items") or []
    for album in items:
        candidate_artist = _spotify_album_artist_name(album)
        if not _artists_match(artist, candidate_artist):
            logger.info(
                "Spotify candidate rejected (artist mismatch): "
                "input %r vs matched %r (album: %r)",
                artist, candidate_artist, album.get("name"),
            )
            continue
        url = (album.get("external_urls") or {}).get("spotify")
        if url and isinstance(url, str) and url.startswith("http"):
            return url
    return None


def _spotify_album_artist_name(album: dict[str, Any]) -> str:
    """Read the display artist-name from a Spotify album response.

    Multi-artist albums (collabs, splits) come back as `artists: [
    {name, ...}, {name, ...} ]`. Concatenate with spaces so the
    artist-match's whole-word substring rule has a chance to fire.
    """
    artists = album.get("artists") or []
    return " ".join(a.get("name") or "" for a in artists).strip()


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

            # Spotify catalog search — fills in spotify_url when MB's
            # URL-relationships table doesn't have one (which is the
            # common case; MB rels are volunteer-contributed and sparse).
            # Skipped if Spotify credentials are unset OR if MB already
            # produced a verified Spotify URL.
            if not result["spotify_url"]:
                result["spotify_url"] = await _spotify_search_album(http, artist, title)

            # Discogs fallback — only when MB didn't find a cover, to save
            # quota. The user gave us a Discogs token only as a fill-in.
            if not result["cover_art_url"]:
                result["cover_art_url"] = await _discogs_cover(http, artist, title)
    except Exception as e:
        # Top-level catch so an enrichment failure can't crash the
        # workflow. Empty result is a valid response from this function.
        logger.error("metadata lookup_release %r — %r raised: %s", artist, title, e)

    return result
