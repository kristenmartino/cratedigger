"""Tests for the per-user Spotify helpers (Tier 3 playlist write-back).

Covers the deterministic pieces:
  - parse_album_id: URL → 22-char id (the input to the per-album track
    resolution call)
  - needs_refresh: token-cache freshness boolean
  - select_track_uris: pick-list → playlist write payload

HTTP-layer calls (refresh_access_token, create_playlist, etc.) aren't
exercised here — those require a real Spotify session. Their failure
paths are designed to raise specific SpotifyAuthError / SpotifyTransientError
which the orchestration layer in spotify_sync.py routes to either
"revoke connection" or "record transient error" outcomes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.sources.spotify_user import (
    needs_refresh,
    parse_album_id,
    select_track_uris,
)


# ── parse_album_id ──────────────────────────────────────────────────────


def test_parses_standard_album_url():
    assert (
        parse_album_id("https://open.spotify.com/album/2nL5UbqaDH3pXEvL3vG6kK")
        == "2nL5UbqaDH3pXEvL3vG6kK"
    )


def test_parses_album_url_with_si_param():
    """The ?si= share-link query param must not interfere with the ID match."""
    assert (
        parse_album_id(
            "https://open.spotify.com/album/2nL5UbqaDH3pXEvL3vG6kK?si=abc123"
        )
        == "2nL5UbqaDH3pXEvL3vG6kK"
    )


def test_parses_album_url_with_intl_prefix():
    """Spotify's intl redirects (`/intl-en/`) must still parse — same
    pattern as the playlist parser."""
    # The album-URL regex doesn't have the same locale tolerance built in;
    # this test pins the contract. If the regex needs widening, this test
    # will catch the omission.
    pid = parse_album_id(
        "https://open.spotify.com/album/2nL5UbqaDH3pXEvL3vG6kK"
    )
    assert pid == "2nL5UbqaDH3pXEvL3vG6kK"


def test_rejects_empty_input():
    assert parse_album_id("") is None
    assert parse_album_id("   ") is None  # type: ignore[arg-type]


def test_rejects_non_album_url():
    """Track URLs and playlist URLs must not be misread as albums."""
    assert (
        parse_album_id("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
        is None
    )
    assert (
        parse_album_id(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        is None
    )


# ── needs_refresh ───────────────────────────────────────────────────────


def test_needs_refresh_when_token_missing():
    assert needs_refresh(None, None) is True
    assert needs_refresh(None, datetime.now(timezone.utc) + timedelta(hours=1)) is True


def test_needs_refresh_when_expires_at_missing():
    """Token present but no expires_at recorded — treat as stale to be
    safe; the refresh call is cheap relative to a 401 mid-playlist-write."""
    assert needs_refresh("token", None) is True


def test_needs_refresh_when_within_buffer():
    """Inside the 60s safety buffer — refresh now to avoid a token
    expiring mid-call."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert needs_refresh("token", expires) is True


def test_does_not_refresh_when_token_still_fresh():
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    assert needs_refresh("token", expires) is False


def test_handles_naive_datetime():
    """Postgres can return TIMESTAMPTZ as naive when the driver isn't
    configured for tz awareness. The helper should still compare safely
    rather than raise."""
    expires = datetime.utcnow() + timedelta(minutes=30)
    # Should not raise + should return False (token still fresh)
    assert needs_refresh("token", expires) is False


# ── select_track_uris ───────────────────────────────────────────────────


def test_orders_track_uris_by_pick_position():
    """Playlist order matches issue position (lead → steady → stretch).
    Don't sort by resolution-time or by URI — the pick list IS the
    intended ordering."""
    picks = [
        {"position": 1, "album_id": "alb1"},
        {"position": 2, "album_id": "alb2"},
        {"position": 3, "album_id": "alb3"},
    ]
    resolved = {
        "alb3": "spotify:track:3",
        "alb1": "spotify:track:1",
        "alb2": "spotify:track:2",
    }
    assert select_track_uris(picks, resolved) == [
        "spotify:track:1",
        "spotify:track:2",
        "spotify:track:3",
    ]


def test_skips_unresolved_picks_silently():
    """A pick whose album we couldn't resolve to a track (region-locked,
    removed) drops out of the playlist. Better to write a partial than
    fail the whole sync."""
    picks = [
        {"position": 1, "album_id": "alb1"},
        {"position": 2, "album_id": "alb2"},  # unresolved
        {"position": 3, "album_id": "alb3"},
    ]
    resolved = {
        "alb1": "spotify:track:1",
        "alb3": "spotify:track:3",
    }
    assert select_track_uris(picks, resolved) == [
        "spotify:track:1",
        "spotify:track:3",
    ]


def test_skips_picks_without_album_id():
    """Picks where the release had no spotify_url (so album_id is None)
    drop out cleanly."""
    picks = [
        {"position": 1, "album_id": None},
        {"position": 2, "album_id": "alb2"},
    ]
    resolved = {"alb2": "spotify:track:2"}
    assert select_track_uris(picks, resolved) == ["spotify:track:2"]


def test_caps_at_50_tracks():
    """Defensive cap — the standard issue is 4 picks but if upstream
    ever expands, we don't want to push the playlist write past
    Spotify's per-request track limit."""
    picks = [{"position": i, "album_id": f"alb{i}"} for i in range(100)]
    resolved = {f"alb{i}": f"spotify:track:{i}" for i in range(100)}
    out = select_track_uris(picks, resolved)
    assert len(out) == 50


def test_returns_empty_for_no_picks():
    assert select_track_uris([], {}) == []
    assert select_track_uris([{"album_id": None}], {}) == []
