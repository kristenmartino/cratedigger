"""Metadata enrichment via MusicBrainz + Discogs.

Most ingested releases come out of crawl with only `(artist, title, url)`
populated — the URL points at the source's RSS entry or shop product page,
not at Bandcamp/Spotify, and cover art is sparse because few RSS feeds
include `media:thumbnail`. Without enrichment, the email's "Listen ↗"
button lands on an article page and most records have no cover.

This module looks up each `(artist, title)` against open music metadata
APIs and returns whatever it finds: a cover art URL, a Bandcamp URL, a
Spotify URL, an Apple Music URL, a YouTube URL. Five nulls is a valid result.

MusicBrainz is primary because:
  - free, no auth, generous rate limit (1 req/sec per User-Agent)
  - explicit URL relationships (Bandcamp, Spotify, Apple Music, etc.)
  - paired with Cover Art Archive (deterministic image URLs by MBID)

iTunes Search API is a free fallback for BOTH apple_music_url and cover art
— no auth, single endpoint returns `collectionViewUrl` (the music.apple.com
URL) and `artworkUrl100` (which we swap to 600x600 for crisp covers).

Discogs is an optional fallback for cover art when MB and iTunes both miss.
Set DISCOGS_TOKEN to enable. Without it, the Discogs path skips cleanly.
We don't ask Discogs for Bandcamp URLs — Discogs doesn't store them.

Rate limit policy:
  - MusicBrainz: 1 req/sec PER USER-AGENT, hard. Enforced here by a
    module-level lock + min-interval, NOT per-task. Concurrent callers
    share the same throttle.
  - Discogs: 60/min auth'd = 1 req/sec equivalent. Same throttle pattern
    via separate lock so a slow MB call doesn't block Discogs.
  - iTunes: undocumented soft ceiling ~20/min per IP. Conservatively pace
    at 2 req/sec — enough headroom for a 50-release crawl in 25s.

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
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

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

# YouTube Data API v3. Free tier 10K units/day; search.list = 100 units
# so ~100 searches/day. We only fire YouTube search as a last-resort
# catalog fallback (article-parse + MB rels + Spotify search all came
# up empty for the release), so we stay well under quota.
_YOUTUBE_LOCK = asyncio.Lock()
_YOUTUBE_LAST_AT = 0.0
_YOUTUBE_MIN_INTERVAL = 0.1  # YouTube's per-second limits are generous

# iTunes Search API. Free, no auth, no quota counter — but Apple has an
# undocumented per-IP soft ceiling around 20 req/min. Pace at 2 req/sec
# (0.5s min interval) which keeps a 50-release crawl under 30s.
_ITUNES_LOCK = asyncio.Lock()
_ITUNES_LAST_AT = 0.0
_ITUNES_MIN_INTERVAL = 0.5


# ── Self-titled shorthand resolution ────────────────────────────────────
#
# Source articles routinely write the title as "S/T" when an album is
# self-titled (same name as the artist). The LLM extraction preserves
# that string into `releases.title`, so the row gets stored as
# `artist="Setting", title="S/T"`. Sending that pair to MB/Spotify/iTunes
# catalog search produces nonsense — there's no record called "S/T" by
# anyone; the search returns whichever album HAS "S/T" in its title,
# which is how Setting matched a Deep Purple Wacken live record.
#
# Resolution: before catalog lookup, detect S/T-style shorthand and
# substitute the artist name (which IS the actual album title for a
# self-titled record). The stored title stays "S/T" so display, dedup,
# and downstream consumers see what the source wrote.

_SELF_TITLED_FORMS = frozenset({
    "s/t", "s.t.", "s t", "st",
    "self titled", "self-titled", "selftitled",
    "untitled",  # rarer but same intent
})


def _resolve_title_for_lookup(title: str, artist: str) -> str:
    """When title is a self-titled shorthand, return the artist name —
    that's the actual album title catalog services index against.
    Otherwise return title unchanged."""
    if title.strip().lower() in _SELF_TITLED_FORMS:
        return artist
    return title


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


# ── YouTube Data API (last-resort catalog fallback) ────────────────────
#
# Different verification problem from Spotify: YouTube videos have free-text
# titles like "Burial — Untrue (Full Album HD)" with the artist often
# embedded but no structured `artists[]` field. We verify by checking if
# the input artist appears as a whole word in either the video title or
# the channel title using the same `_artists_match` rule.
#
# Bias toward full-album uploads with the `q=` term, but YouTube ranks by
# its own relevance signal so we still check the top few results and pick
# the first that verifies on artist.


def _youtube_candidate_artist(snippet: dict[str, Any]) -> str:
    """Combine channel + video title into one string for artist-match
    verification. YouTube doesn't have a structured `artist` field on
    videos, so we hand the whole label string to _artists_match and
    let the whole-word substring rule decide if the input artist is
    referenced anywhere.

    Concatenating with a space joiner means the whole-word rule never
    collapses "Fred Again" against "FredAgain" — same protection as
    elsewhere.
    """
    return " ".join((
        snippet.get("channelTitle") or "",
        snippet.get("title") or "",
    )).strip()


async def _youtube_search(
    http: AsyncSession, artist: str, title: str
) -> str | None:
    """Search YouTube for a video matching (artist, title). Return a
    canonical youtube.com/watch?v=ID URL, or None on miss/error.

    Skipped if YOUTUBE_API_KEY is unset. Each call costs 100 quota units
    (10K/day free tier) so this is a last-resort fallback — only invoke
    when bandcamp/spotify/article-parsing all came up empty.
    """
    key = settings.youtube_api_key
    if not key:
        return None

    await _throttle(_YOUTUBE_LOCK, "_YOUTUBE_LAST_AT", _YOUTUBE_MIN_INTERVAL)
    try:
        # `q` is a free-text query; "album" biases toward full uploads.
        # `type=video` filters out channels and playlists.
        resp = await http.get(
            f"{YOUTUBE_API_BASE}/search",
            params={
                "part": "snippet",
                "q": f"{artist} {title} album",
                "type": "video",
                "maxResults": "5",
                "key": key,
            },
            allow_redirects=True,
        )
        if resp.status_code != 200:
            logger.info(
                "YouTube search %r — %r returned %d: %s",
                artist, title, resp.status_code, resp.text[:200],
            )
            return None
        data = resp.json()
    except Exception as e:
        logger.warning("YouTube search %r — %r raised: %s", artist, title, e)
        return None

    for item in data.get("items") or []:
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        candidate_artist = _youtube_candidate_artist(snippet)
        if not _artists_match(artist, candidate_artist):
            logger.info(
                "YouTube candidate rejected (artist mismatch): "
                "input %r vs matched %r",
                artist, candidate_artist,
            )
            continue
        return f"https://www.youtube.com/watch?v={video_id}"

    return None


# ── iTunes Search (free, no auth — both cover art + Apple Music URL) ───
#
# Apple's Search API is unauthenticated and returns both `collectionViewUrl`
# (the music.apple.com URL we want as a listen target) and `artworkUrl100`
# (cover image at 100x100) in one call. Since we get both for free, this
# slot is doubly useful: a fallback for cover art AND a new listen-target
# platform between Spotify and YouTube in the editorial preference chain.
#
# Artwork URLs are CDN-served at `.../100x100bb.jpg`. Replacing 100x100
# with 600x600 returns the same image at higher resolution — same trick
# the Apple Music web client uses.


_ITUNES_ARTWORK_DIM_RE = re.compile(r"/\d+x\d+(bb)?\.(jpg|png)$")


def _itunes_artwork_hires(artwork_url: str) -> str:
    """Upgrade an iTunes artworkUrl100 to 600x600. Same CDN, just a
    different size token in the path. Returns input unchanged if the
    URL doesn't match the expected pattern."""
    return _ITUNES_ARTWORK_DIM_RE.sub(r"/600x600\1.\2", artwork_url)


async def _itunes_search(
    http: AsyncSession, artist: str, title: str
) -> tuple[str | None, str | None]:
    """Search Apple's iTunes catalog for (artist, title).

    Returns (apple_music_url, cover_art_url) — either or both may be None.

    Free, no auth. Same `_artists_match` verification as MB/Spotify: the
    response has an `artistName` field per result, so the check is direct.
    """
    await _throttle(_ITUNES_LOCK, "_ITUNES_LAST_AT", _ITUNES_MIN_INTERVAL)
    try:
        resp = await http.get(
            ITUNES_SEARCH_URL,
            params={
                "term": f"{artist} {title}",
                "entity": "album",
                "limit": "5",
            },
            allow_redirects=True,
        )
        if resp.status_code != 200:
            logger.info(
                "iTunes search %r — %r returned %d", artist, title, resp.status_code
            )
            return None, None
        data = resp.json()
    except Exception as e:
        logger.warning("iTunes search %r — %r raised: %s", artist, title, e)
        return None, None

    for item in data.get("results") or []:
        candidate_artist = item.get("artistName") or ""
        if not _artists_match(artist, candidate_artist):
            logger.info(
                "iTunes candidate rejected (artist mismatch): "
                "input %r vs matched %r (album: %r)",
                artist, candidate_artist, item.get("collectionName"),
            )
            continue
        url = item.get("collectionViewUrl") or None
        artwork = item.get("artworkUrl100") or None
        if artwork:
            artwork = _itunes_artwork_hires(artwork)
        if url or artwork:
            return url, artwork
    return None, None


# ── URL verification ────────────────────────────────────────────────────
#
# The catalog-search paths (Spotify search, iTunes search, Discogs) all
# call _artists_match on the candidate before accepting. But platform URLs
# can also arrive from paths that DON'T verify:
#   - MusicBrainz URL relations (volunteer-contributed; can be wrong)
#   - article_media.py (extracts first embedded link in a source article;
#     no association with our target record)
#
# Bug case from production (Setting — S/T): the Aquarium Drunkard piece
# had a Spotify embed for a Deep Purple Wacken live record; article-media
# parsing grabbed it as if it were Setting's link. Reader clicked Listen,
# got Deep Purple.
#
# The fix: after lookup_release has collected URLs from every source,
# verify each one against the target artist by fetching that platform's
# metadata. Mismatches get dropped before persisting. Conservative when
# verification is impossible (missing creds, network error): preserve the
# URL rather than risk false drops.


_SPOTIFY_ALBUM_ID_RE = re.compile(r"open\.spotify\.com/album/([A-Za-z0-9]{22})")
_APPLE_MUSIC_ID_RE = re.compile(r"music\.apple\.com/[^/]+/album/[^/]+/(\d+)")
_BANDCAMP_OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]+)"', re.IGNORECASE
)


async def _verify_spotify_url(
    http: AsyncSession, url: str, target_artist: str
) -> bool:
    """Fetch Spotify's album metadata and verify the artist via
    `_artists_match`. Returns True when the artist verifies OR when we
    can't fetch the album at all (preserve URL on infrastructure errors;
    only drop on a confirmed mismatch)."""
    m = _SPOTIFY_ALBUM_ID_RE.search(url)
    if not m:
        return False  # malformed URL; drop
    album_id = m.group(1)
    token = await _spotify_token(http)
    if not token:
        return True  # no creds — can't verify, don't drop existing data
    await _throttle(_SPOTIFY_LOCK, "_SPOTIFY_LAST_AT", _SPOTIFY_MIN_INTERVAL)
    try:
        resp = await http.get(
            f"{SPOTIFY_API_BASE}/albums/{album_id}",
            headers={"Authorization": f"Bearer {token}"},
            allow_redirects=True,
        )
    except Exception as e:
        logger.warning("verify spotify %s raised: %s", album_id, e)
        return True  # transient; preserve

    if resp.status_code == 404 or resp.status_code == 410:
        logger.info("verify spotify %s gone (%d) — dropping", album_id, resp.status_code)
        return False
    if resp.status_code != 200:
        logger.info("verify spotify %s returned %d — preserving", album_id, resp.status_code)
        return True

    album = resp.json()
    candidate = _spotify_album_artist_name(album)
    if not _artists_match(target_artist, candidate):
        logger.info(
            "verify spotify dropped: target %r vs album-artist %r (album=%s)",
            target_artist, candidate, album_id,
        )
        return False
    return True


async def _verify_apple_music_url(
    http: AsyncSession, url: str, target_artist: str
) -> bool:
    """Fetch iTunes Lookup for the album ID embedded in a music.apple.com
    URL and verify the artistName via `_artists_match`."""
    m = _APPLE_MUSIC_ID_RE.search(url)
    if not m:
        return False
    collection_id = m.group(1)
    await _throttle(_ITUNES_LOCK, "_ITUNES_LAST_AT", _ITUNES_MIN_INTERVAL)
    try:
        resp = await http.get(
            "https://itunes.apple.com/lookup",
            params={"id": collection_id},
            allow_redirects=True,
        )
    except Exception as e:
        logger.warning("verify apple_music %s raised: %s", collection_id, e)
        return True
    if resp.status_code != 200:
        return True
    data = resp.json()
    results = data.get("results") or []
    if not results:
        return False
    candidate = results[0].get("artistName") or ""
    if not _artists_match(target_artist, candidate):
        logger.info(
            "verify apple_music dropped: target %r vs artistName %r (id=%s)",
            target_artist, candidate, collection_id,
        )
        return False
    return True


async def _verify_bandcamp_url(
    http: AsyncSession, url: str, target_artist: str
) -> bool:
    """Fetch the Bandcamp page and parse the og:title meta tag for the
    artist name. Bandcamp's og:title format is `Album Title, by Artist`.

    On any error, returns True (preserve) rather than drop — Bandcamp HTML
    is less standardized than the platform APIs and our parser may simply
    not recognize the shape.
    """
    try:
        resp = await http.get(url, allow_redirects=True)
    except Exception as e:
        logger.warning("verify bandcamp %s raised: %s", url, e)
        return True
    if resp.status_code == 404 or resp.status_code == 410:
        logger.info("verify bandcamp %s gone (%d) — dropping", url, resp.status_code)
        return False
    if resp.status_code != 200:
        return True
    html = resp.text
    m = _BANDCAMP_OG_TITLE_RE.search(html)
    if not m:
        return True
    og_title = m.group(1)
    if ", by " not in og_title:
        return True
    candidate = og_title.rsplit(", by ", 1)[-1].strip()
    if not _artists_match(target_artist, candidate):
        logger.info(
            "verify bandcamp dropped: target %r vs page-artist %r (url=%s)",
            target_artist, candidate, url,
        )
        return False
    return True


async def _verify_listen_urls(
    http: AsyncSession, result: dict[str, str | None], target_artist: str
) -> None:
    """Verify each platform URL in `result` against `target_artist`. Drops
    URLs that fail verification (sets the field to None). Modifies `result`
    in place.

    YouTube URLs aren't verified — video metadata doesn't expose a clean
    `artist` field, and the YouTube search path already runs
    `_artists_match` on channelTitle+title before producing a URL, so the
    false-positive class that hits Spotify/Bandcamp/Apple (via MB rels and
    article-media parsing) doesn't apply to YouTube the same way.
    """
    if result["spotify_url"]:
        ok = await _verify_spotify_url(http, result["spotify_url"], target_artist)
        if not ok:
            result["spotify_url"] = None
    if result["apple_music_url"]:
        ok = await _verify_apple_music_url(http, result["apple_music_url"], target_artist)
        if not ok:
            result["apple_music_url"] = None
    if result["bandcamp_url"]:
        ok = await _verify_bandcamp_url(http, result["bandcamp_url"], target_artist)
        if not ok:
            result["bandcamp_url"] = None


# ── Public API ──────────────────────────────────────────────────────────


async def lookup_release(artist: str, title: str) -> dict[str, str | None]:
    """Return whatever metadata we can find for (artist, title).

    Schema:
        {"cover_art_url": str|None, "bandcamp_url": str|None,
         "spotify_url": str|None, "apple_music_url": str|None,
         "youtube_url": str|None, "mbid": str|None}

    All fields may be None — that's a valid result for releases the public
    metadata layer doesn't index (small-label drops, mailing-list releases,
    super-fresh records that haven't propagated yet).

    Fallback order, in priority:
      MB release-group + URL-rels   (cover_art_url + bandcamp_url + spotify_url)
      Spotify catalog search        (spotify_url, if MB didn't have one)
      iTunes Search                 (apple_music_url + cover_art_url fallback)
      YouTube Data API search       (youtube_url, last-resort, quota-budgeted)
      Discogs                       (cover_art_url fallback only)
    """
    result: dict[str, str | None] = {
        "cover_art_url": None,
        "bandcamp_url": None,
        "spotify_url": None,
        "apple_music_url": None,
        "youtube_url": None,
        "mbid": None,
    }
    if not (artist and title):
        return result

    headers = {
        "User-Agent": settings.crawler_user_agent,
        "Accept": "application/json",
    }

    # Self-titled shorthand (`title == "S/T"`) gets resolved to the
    # artist name for catalog lookups — every external service indexes
    # the real title, not the abbreviation. Only the lookup queries use
    # the resolved string; the stored title and downstream display stay
    # whatever the source wrote.
    search_title = _resolve_title_for_lookup(title, artist)

    try:
        async with AsyncSession(
            timeout=15.0, headers=headers, impersonate=IMPERSONATE
        ) as http:
            mbid = await _mb_search_release_group(http, artist, search_title)
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
                result["spotify_url"] = await _spotify_search_album(http, artist, search_title)

            # iTunes Search — free, no auth, gives us both apple_music_url
            # AND a cover-art fallback in a single call. Fire whenever we
            # still need either: missing Apple Music URL OR missing cover.
            # The same response usefully fills both gaps, so the gating is
            # "is there still anything iTunes might provide" rather than
            # two separate decisions.
            if not result["apple_music_url"] or not result["cover_art_url"]:
                apple_url, apple_cover = await _itunes_search(http, artist, search_title)
                if not result["apple_music_url"]:
                    result["apple_music_url"] = apple_url
                if not result["cover_art_url"]:
                    result["cover_art_url"] = apple_cover

            # YouTube Data API — last-resort listen-target fallback. Only
            # fires when we have no Bandcamp AND no Spotify AND no Apple
            # Music URL for this release; otherwise the existing listen-URL
            # chain already has a higher-quality target, and YouTube quota
            # is precious (10K units/day, 100 per search).
            if (
                not result["bandcamp_url"]
                and not result["spotify_url"]
                and not result["apple_music_url"]
            ):
                result["youtube_url"] = await _youtube_search(http, artist, search_title)

            # Discogs fallback — only when MB and iTunes both missed a
            # cover, to save quota. The user gave us a Discogs token only
            # as a fill-in.
            if not result["cover_art_url"]:
                result["cover_art_url"] = await _discogs_cover(http, artist, search_title)

            # Verify every Listen-target URL against the platform's own
            # metadata. Catches URLs that bypassed the per-search artist
            # check (MusicBrainz URL relations, article-media parsing).
            # Drops mismatches; preserves URLs when verification fails
            # for infrastructure reasons (network, missing creds).
            await _verify_listen_urls(http, result, artist)
    except Exception as e:
        # Top-level catch so an enrichment failure can't crash the
        # workflow. Empty result is a valid response from this function.
        logger.error("metadata lookup_release %r — %r raised: %s", artist, title, e)

    return result
