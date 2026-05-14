"""Spotify playlist sync — orchestrates the per-user write of this week's
picks into a rolling Crate Digger playlist.

Called either:
  - Synchronously from /v1/sync-spotify-playlist (manual / cron-triggered)
  - Fire-and-forget from the post-issue cron path

For a given user_id:
  1. Look up user_spotify_connections row. Skip if revoked OR absent.
  2. Refresh the access token if needed (revoke connection on auth error).
  3. Pull the user's latest issue's 4 non-withheld picks. Skip if no
     issue exists yet.
  4. Resolve each pick's album → first track URI (skip picks with no
     spotify_url or unresolvable albums).
  5. Create playlist if first sync, else replace tracks. Handle
     "playlist deleted" by re-creating.
  6. Update last_sync_at / last_error on the connection row.

Top-level errors are caught and logged — the caller never crashes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.db import get_pool
from agent.sources.spotify_user import (
    SpotifyAuthError,
    SpotifyTransientError,
    create_playlist,
    get_album_first_track_uri,
    make_http_session,
    needs_refresh,
    parse_album_id,
    refresh_access_token,
    replace_playlist_tracks,
    select_track_uris,
)

logger = logging.getLogger("cratedigger-agent.spotify_sync")


async def _load_connection(pool, user_id: str) -> dict[str, Any] | None:
    """Load the active Spotify connection for a user, or None."""
    row = await pool.fetchrow(
        """
        SELECT id::text,
               spotify_user_id, refresh_token,
               access_token, access_token_expires_at,
               playlist_id, revoked
          FROM user_spotify_connections
         WHERE user_id = $1::uuid
        """,
        user_id,
    )
    if row is None:
        return None
    return dict(row)


async def _mark_revoked(pool, user_id: str, reason: str) -> None:
    await pool.execute(
        """
        UPDATE user_spotify_connections
           SET revoked = TRUE, last_error = $2
         WHERE user_id = $1::uuid
        """,
        user_id, reason[:500],
    )


async def _record_error(pool, user_id: str, message: str) -> None:
    await pool.execute(
        """
        UPDATE user_spotify_connections
           SET last_error = $2
         WHERE user_id = $1::uuid
        """,
        user_id, message[:500],
    )


async def _record_success(pool, user_id: str, playlist_id: str) -> None:
    await pool.execute(
        """
        UPDATE user_spotify_connections
           SET playlist_id = $2,
               last_sync_at = NOW(),
               last_error = NULL
         WHERE user_id = $1::uuid
        """,
        user_id, playlist_id,
    )


async def _store_refreshed_tokens(
    pool,
    user_id: str,
    access_token: str,
    expires_in: int,
    new_refresh: str | None,
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    if new_refresh:
        await pool.execute(
            """
            UPDATE user_spotify_connections
               SET access_token = $2,
                   access_token_expires_at = $3,
                   refresh_token = $4
             WHERE user_id = $1::uuid
            """,
            user_id, access_token, expires_at, new_refresh,
        )
    else:
        await pool.execute(
            """
            UPDATE user_spotify_connections
               SET access_token = $2,
                   access_token_expires_at = $3
             WHERE user_id = $1::uuid
            """,
            user_id, access_token, expires_at,
        )


async def _latest_issue_picks(pool, user_id: str) -> list[dict[str, Any]]:
    """The 4 non-withheld picks from the user's most recent issue.

    Returns empty list if no issues exist yet. Picks come back in
    position order so the playlist preserves lead → steady → stretch
    sequencing."""
    rows = await pool.fetch(
        """
        SELECT r.position, r.category::text AS category,
               rel.spotify_url, rel.artist, rel.title
          FROM recommendations r
          JOIN issues i ON i.id = r.issue_id
          JOIN releases rel ON rel.id = r.release_id
         WHERE i.user_id = $1::uuid
           AND r.category != 'withheld'
           AND i.id = (
               SELECT id FROM issues
                WHERE user_id = $1::uuid
                ORDER BY issue_number DESC
                LIMIT 1
           )
         ORDER BY r.position
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def sync_playlist_for_user(user_id: str) -> dict[str, Any]:
    """Top-level orchestration. Returns a summary dict:

      {"status": "synced"|"skipped"|"revoked"|"error",
       "playlist_id": str|None,
       "tracks_written": int,
       "message": str|None}

    Never raises — failure modes are captured in the return value and
    in user_spotify_connections.last_error so ops have visibility.
    """
    pool = await get_pool()

    conn = await _load_connection(pool, user_id)
    if conn is None:
        return {"status": "skipped", "message": "no connection", "tracks_written": 0}
    if conn["revoked"]:
        return {"status": "skipped", "message": "connection revoked", "tracks_written": 0}

    picks = await _latest_issue_picks(pool, user_id)
    if not picks:
        return {"status": "skipped", "message": "no issues yet", "tracks_written": 0}

    # Annotate picks with album_id so the helper can dedupe + select
    pick_payload = []
    for p in picks:
        album_id = parse_album_id(p.get("spotify_url") or "")
        pick_payload.append({**p, "album_id": album_id})

    async with make_http_session() as http:
        # Step 1: ensure a fresh access token
        access_token = conn["access_token"]
        expires_at = conn["access_token_expires_at"]
        if needs_refresh(access_token, expires_at):
            try:
                access_token, expires_in, new_refresh = await refresh_access_token(
                    http, conn["refresh_token"]
                )
            except SpotifyAuthError as e:
                msg = f"refresh_token rejected: {e}"
                logger.warning("Spotify sync revoking user %s: %s", user_id, msg)
                await _mark_revoked(pool, user_id, msg)
                return {"status": "revoked", "message": msg, "tracks_written": 0}
            except SpotifyTransientError as e:
                msg = f"refresh transient error: {e}"
                logger.warning("Spotify sync transient %s: %s", user_id, msg)
                await _record_error(pool, user_id, msg)
                return {"status": "error", "message": msg, "tracks_written": 0}
            await _store_refreshed_tokens(
                pool, user_id, access_token, expires_in, new_refresh
            )

        # Step 2: resolve each pick's album → first track URI
        resolved: dict[str, str] = {}
        for p in pick_payload:
            aid = p["album_id"]
            if not aid:
                continue
            uri = await get_album_first_track_uri(http, access_token, aid)
            if uri:
                resolved[aid] = uri

        track_uris = select_track_uris(pick_payload, resolved)
        if not track_uris:
            msg = "no resolvable tracks for this week's picks"
            await _record_error(pool, user_id, msg)
            return {"status": "skipped", "message": msg, "tracks_written": 0}

        # Step 3: write to playlist (create on first sync OR if deleted)
        playlist_id = conn["playlist_id"]
        try:
            if playlist_id:
                ok = await replace_playlist_tracks(
                    http, access_token, playlist_id, track_uris
                )
                if not ok:
                    # User deleted the playlist — recreate
                    playlist_id = await create_playlist(
                        http, access_token, conn["spotify_user_id"]
                    )
                    ok = await replace_playlist_tracks(
                        http, access_token, playlist_id, track_uris
                    )
                    if not ok:
                        raise SpotifyTransientError("new playlist also 404'd")
            else:
                playlist_id = await create_playlist(
                    http, access_token, conn["spotify_user_id"]
                )
                ok = await replace_playlist_tracks(
                    http, access_token, playlist_id, track_uris
                )
                if not ok:
                    raise SpotifyTransientError("playlist 404 right after create")
        except SpotifyTransientError as e:
            msg = f"playlist write: {e}"
            logger.warning("Spotify sync write failed %s: %s", user_id, msg)
            await _record_error(pool, user_id, msg)
            return {"status": "error", "message": msg, "tracks_written": 0}

        await _record_success(pool, user_id, playlist_id)
        return {
            "status": "synced",
            "playlist_id": playlist_id,
            "tracks_written": len(track_uris),
            "message": None,
        }


async def sync_for_all_connected_users() -> list[dict[str, Any]]:
    """Sync every user with an active (non-revoked) connection. Used by
    the post-Sunday cron path. Concurrency 3 to keep us under Spotify's
    rate limits even at user count scale."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT user_id::text FROM user_spotify_connections WHERE revoked = FALSE"
    )
    sem = asyncio.Semaphore(3)

    async def _one(uid: str) -> dict[str, Any]:
        async with sem:
            try:
                result = await sync_playlist_for_user(uid)
                return {"user_id": uid, **result}
            except Exception as e:
                logger.error("sync_playlist_for_user %s raised: %s", uid, e)
                return {"user_id": uid, "status": "error", "message": str(e)}

    return await asyncio.gather(*(_one(r["user_id"]) for r in rows))
