"""Per-user Spotify operations for Tier 3 — playlist write-back.

Distinct from `agent/sources/metadata.py`'s Spotify path, which uses the
app's Client Credentials grant (server-to-server, no user). This module
acts as the LOGGED-IN USER via their OAuth refresh token. Two scopes
are sufficient for the rolling-playlist write:
  - playlist-modify-public
  - user-read-email

Token lifecycle:
  - Long-lived refresh_token is stored in user_spotify_connections.
    Spotify can revoke it at any time (user clicks "Remove access" in
    their account dashboard) — refresh attempts return 400/401 in that
    case, and we mark the connection revoked=TRUE so we don't retry.
  - Access tokens are 1-hour TTL. We cache them in the same row
    (access_token, access_token_expires_at) and refresh proactively if
    less than 60s remain — same buffer pattern as the Client Credentials
    cache in metadata.py.

Playlist lifecycle:
  - First sync: create a public playlist "Crate Digger" on the user's
    account, store the id, fill it with this week's tracks.
  - Subsequent syncs: replace ALL playlist tracks with this week's
    picks (rolling, not appending — keeps the playlist fresh).
  - User deleted the playlist? Spotify returns 404 on the replace. We
    detect this and create a new one, persist the new id.

Failure modes are surfaced via `last_error` on the connection row so
ops can grep recent failures. Top-level errors don't crash the caller;
the rest of the issue still ships.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession

logger = logging.getLogger("cratedigger-agent.spotify_user")

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

PLAYLIST_NAME = "Crate Digger"
PLAYLIST_DESCRIPTION = (
    "This week's picks from Crate Digger — auto-updated each Sunday."
)

# Spotify album URL → ID
_ALBUM_ID_RE = re.compile(r"open\.spotify\.com/album/([A-Za-z0-9]{22})")


def parse_album_id(spotify_url: str) -> str | None:
    """Extract the 22-char album ID from `https://open.spotify.com/album/<id>`."""
    if not spotify_url:
        return None
    m = _ALBUM_ID_RE.search(spotify_url)
    return m.group(1) if m else None


class SpotifyAuthError(Exception):
    """Refresh token invalid — connection should be marked revoked."""


class SpotifyTransientError(Exception):
    """5xx / network / rate-limited — retryable, don't mark revoked."""


async def refresh_access_token(
    http: AsyncSession,
    refresh_token: str,
) -> tuple[str, int, str | None]:
    """Exchange a refresh_token for a fresh access_token.

    Returns (access_token, expires_in_seconds, new_refresh_token | None).
    Spotify occasionally rotates the refresh token; when it does, the
    caller must persist the new one. When it doesn't, the old token
    stays valid.

    Raises:
      SpotifyAuthError on 400/401 — refresh_token is dead; revoke.
      SpotifyTransientError on 5xx / network / unexpected response.
    """
    if not (settings.spotify_client_id and settings.spotify_client_secret):
        raise SpotifyTransientError("Spotify credentials not configured")

    creds = base64.b64encode(
        f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
    ).decode()

    try:
        resp = await http.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allow_redirects=True,
        )
    except Exception as e:
        raise SpotifyTransientError(f"Network error: {e}") from e

    if resp.status_code in (400, 401):
        # Spotify returns 400 invalid_grant when the refresh token is
        # revoked. 401 also possible if our app credentials drifted.
        body = resp.text[:300] if hasattr(resp, "text") else ""
        raise SpotifyAuthError(f"Refresh rejected ({resp.status_code}): {body}")
    if resp.status_code != 200:
        raise SpotifyTransientError(
            f"Refresh returned {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    token = data.get("access_token")
    expires_in = int(data.get("expires_in") or 3600)
    new_refresh = data.get("refresh_token")  # may be absent
    if not token:
        raise SpotifyTransientError("Token response missing access_token")
    return token, expires_in, new_refresh


async def get_album_first_track_uri(
    http: AsyncSession, access_token: str, album_id: str
) -> str | None:
    """Resolve an album to a representative track URI.

    Playlists hold tracks, not albums. We use the first track (usually
    the A-side / lead single — most representative of the release).

    Returns None if the album isn't accessible (region-locked, removed,
    market-restricted). Callers should skip that pick rather than fail
    the whole playlist write.
    """
    try:
        resp = await http.get(
            f"{SPOTIFY_API_BASE}/albums/{album_id}/tracks",
            params={"limit": "1"},
            headers={"Authorization": f"Bearer {access_token}"},
            allow_redirects=True,
        )
    except Exception as e:
        logger.warning("album tracks fetch %s raised: %s", album_id, e)
        return None
    if resp.status_code != 200:
        logger.info(
            "album tracks %s returned %d", album_id, resp.status_code,
        )
        return None
    items = resp.json().get("items") or []
    if not items:
        return None
    uri = items[0].get("uri")
    return uri if isinstance(uri, str) and uri.startswith("spotify:track:") else None


async def create_playlist(
    http: AsyncSession,
    access_token: str,
    spotify_user_id: str,
) -> str:
    """Create the rolling Crate Digger playlist on the user's account.
    Returns the new playlist id."""
    resp = await http.post(
        f"{SPOTIFY_API_BASE}/users/{spotify_user_id}/playlists",
        json={
            "name": PLAYLIST_NAME,
            "description": PLAYLIST_DESCRIPTION,
            "public": True,
        },
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        allow_redirects=True,
    )
    if resp.status_code not in (200, 201):
        raise SpotifyTransientError(
            f"Create playlist returned {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    pid = data.get("id")
    if not isinstance(pid, str):
        raise SpotifyTransientError("Create playlist response missing id")
    return pid


async def replace_playlist_tracks(
    http: AsyncSession,
    access_token: str,
    playlist_id: str,
    track_uris: list[str],
) -> bool:
    """Replace ALL tracks in the playlist with the given URIs.

    Returns False if the playlist no longer exists (404) — caller should
    recreate. Raises SpotifyTransientError on other non-2xx.
    """
    resp = await http.put(
        f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks",
        json={"uris": track_uris},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        allow_redirects=True,
    )
    if resp.status_code == 404:
        return False
    if resp.status_code not in (200, 201):
        raise SpotifyTransientError(
            f"Replace tracks returned {resp.status_code}: {resp.text[:300]}"
        )
    return True


def needs_refresh(
    access_token: str | None,
    expires_at: datetime | None,
    buffer_seconds: int = 60,
) -> bool:
    """Whether the cached access token should be refreshed before use.

    True if missing, or expires within the buffer window. Same 60s buffer
    the Client Credentials cache uses in metadata.py."""
    if not access_token or not expires_at:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc) + timedelta(seconds=buffer_seconds)


def make_http_session() -> AsyncSession:
    """Construct the AsyncSession used for Spotify user-API calls. Same
    UA + impersonation as the catalog-search path."""
    return AsyncSession(
        timeout=15.0,
        headers={
            "User-Agent": settings.crawler_user_agent,
            "Accept": "application/json",
        },
        impersonate=IMPERSONATE,
    )


# ── Pure helpers (no I/O) ────────────────────────────────────────────────


def select_track_uris(
    picks: list[dict[str, Any]],
    resolved: dict[str, str],
) -> list[str]:
    """Build the final track URI list for a playlist write.

    `picks` is the ordered list of recommendations (lead → steady → stretch);
    `resolved` maps album_id → track URI for whichever picks we could resolve.
    Picks without a Spotify URL or without a resolvable track are silently
    skipped (we'd rather write 3 tracks than fail the whole sync).

    Returns track URIs in pick order, capped at 50 (well above the 4-pick
    output but defensive against future expansion).
    """
    out: list[str] = []
    for pick in picks:
        album_id = pick.get("album_id")
        if not album_id:
            continue
        uri = resolved.get(album_id)
        if uri:
            out.append(uri)
        if len(out) >= 50:
            break
    return out
