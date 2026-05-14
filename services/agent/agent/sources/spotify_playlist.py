"""Spotify playlist parsing for the onboarding flow.

Given a public Spotify playlist URL (or ID), this module returns the unique
artist names appearing in the playlist. The web onboarding form pre-fills
the artist textarea with this list so a user can paste their "Most Played"
or favorite-discoveries playlist instead of typing 20-30 names by hand.

Uses the same Client Credentials grant (server-to-server, no user OAuth)
that powers `metadata._spotify_search_album`. Public playlists are
readable with app-level credentials; private playlists require user
OAuth and aren't supported here.

Limits:
- First page of tracks only (Spotify default = 100). 100 tracks is
  plenty for a taste seed; we don't paginate to keep the call fast and
  the artist list de-duped to something a human can review.
- Artists capped at MAX_ARTISTS so the onboarding form (which validates
  10-50) gets a useful number whether the playlist has 20 tracks or 200.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession
from agent.sources.metadata import SPOTIFY_API_BASE, _spotify_token

logger = logging.getLogger("cratedigger-agent.spotify_playlist")

# 22-char base62, the standard Spotify ID shape
_PLAYLIST_ID_RE = re.compile(r"[A-Za-z0-9]{22}")

# Spotify Web URLs we accept. The function tolerates query strings, region
# prefixes (locale shorthand like `/de/` AND intl redirects like `/intl-en/`),
# and the spotify:playlist:ID URI form.
_PLAYLIST_PATH_RE = re.compile(
    r"open\.spotify\.com/(?:[a-z-]+/)?playlist/([A-Za-z0-9]{22})"
)
_PLAYLIST_URI_RE = re.compile(r"spotify:playlist:([A-Za-z0-9]{22})")

# Caps how many unique artists we return. Matches the upper bound of the
# onboarding form's MAX_ARTISTS so we don't hand back more than the form
# will accept anyway.
MAX_ARTISTS = 50


def parse_playlist_url(url_or_id: str) -> str | None:
    """Extract the playlist ID from any of the shapes a user might paste.

    Accepts:
      - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
      - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc
      - https://open.spotify.com/intl-en/playlist/37i9dQZF1DXcBWIGoYBM5M
      - spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
      - 37i9dQZF1DXcBWIGoYBM5M    (just the ID)

    Returns the 22-char ID, or None if no recognizable playlist ID is
    present in the input.
    """
    if not url_or_id:
        return None
    s = url_or_id.strip()

    m = _PLAYLIST_PATH_RE.search(s)
    if m:
        return m.group(1)

    m = _PLAYLIST_URI_RE.search(s)
    if m:
        return m.group(1)

    # Bare 22-char ID — only accept if the WHOLE string matches, so a
    # random 22-char substring in a query string can't fool us.
    if _PLAYLIST_ID_RE.fullmatch(s):
        return s

    return None


def extract_artists_from_tracks(payload: dict[str, Any]) -> list[str]:
    """Pull unique artist names out of a Spotify playlist-tracks response.

    Preserves the order of first appearance — the user's most-listened
    artists usually sit at the top of their playlists, so head-of-list
    order tends to encode taste-priority.

    Handles the playlist response's quirks:
      - `items[].track` is None for tracks the user can't access
        (deleted from Spotify, region-locked, etc.) — skip those
      - `items[].track.artists` is a list; each `artists[].name` is the
        display name
      - Truncates to MAX_ARTISTS unique artists
    """
    seen: set[str] = set()
    out: list[str] = []
    items = payload.get("items") or payload.get("tracks", {}).get("items") or []
    for item in items:
        track = (item or {}).get("track") or {}
        for artist in track.get("artists") or []:
            name = (artist.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
            if len(out) >= MAX_ARTISTS:
                return out
    return out


async def fetch_playlist_artists(playlist_id: str) -> list[str]:
    """Hit Spotify's `/v1/playlists/{id}` and return up to MAX_ARTISTS
    unique artist names.

    Returns an empty list on:
      - Spotify credentials unset
      - Playlist not found / private (404)
      - Spotify down (5xx)
      - Network error
    Callers should treat empty as "couldn't load this playlist" and let
    the user fall back to manual entry. Exceptions never escape here —
    the onboarding API needs deterministic success/empty paths.
    """
    headers = {
        "User-Agent": settings.crawler_user_agent,
        "Accept": "application/json",
    }
    try:
        async with AsyncSession(
            timeout=15.0, headers=headers, impersonate=IMPERSONATE
        ) as http:
            token = await _spotify_token(http)
            if not token:
                logger.warning("Spotify credentials unset — cannot fetch playlist")
                return []
            # `fields` trims the response to just what we need. Without it
            # Spotify returns track metadata that easily pushes responses
            # over 200KB on long playlists.
            resp = await http.get(
                f"{SPOTIFY_API_BASE}/playlists/{playlist_id}",
                params={"fields": "tracks.items(track(artists(name)))"},
                headers={"Authorization": f"Bearer {token}"},
                allow_redirects=True,
            )
            if resp.status_code == 404:
                logger.info("Spotify playlist %s not found / private", playlist_id)
                return []
            if resp.status_code != 200:
                logger.warning(
                    "Spotify playlist fetch %s returned %d: %s",
                    playlist_id, resp.status_code, resp.text[:200],
                )
                return []
            data = resp.json()
    except Exception as e:
        logger.warning("Spotify playlist fetch %s raised: %s", playlist_id, e)
        return []

    return extract_artists_from_tracks(data.get("tracks") or {})
