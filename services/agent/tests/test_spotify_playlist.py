"""Tests for the Spotify playlist parser used by Tier 2 onboarding.

Covers the deterministic pieces:
  - parse_playlist_url: URL/URI/ID variants the user might paste
  - extract_artists_from_tracks: response-shape transform + dedup + cap

The HTTP call inside fetch_playlist_artists isn't exercised here — that
requires real Spotify credentials and a live playlist. The
empty-credentials short-circuit IS tested (it's a code path the
onboarding form will hit when SPOTIFY_CLIENT_ID is unset).
"""
from __future__ import annotations

import asyncio

from agent.sources.spotify_playlist import (
    MAX_ARTISTS,
    extract_artists_from_tracks,
    fetch_playlist_artists,
    parse_playlist_url,
)


# ── parse_playlist_url ──────────────────────────────────────────────────


def test_parses_standard_web_url():
    url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    assert parse_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"


def test_parses_url_with_query_string():
    """`?si=` tracking param is what Spotify's share button appends —
    the most common shape users actually paste."""
    url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123def456"
    assert parse_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"


def test_parses_url_with_intl_prefix():
    """Spotify's international redirects insert `/intl-en/` etc."""
    url = "https://open.spotify.com/intl-en/playlist/37i9dQZF1DXcBWIGoYBM5M"
    assert parse_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"


def test_parses_spotify_uri():
    """Desktop client → right-click → Copy Spotify URI returns this shape."""
    uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
    assert parse_playlist_url(uri) == "37i9dQZF1DXcBWIGoYBM5M"


def test_parses_bare_id():
    """Some users will trim everything but the ID. Accept full-match
    only — a 22-char substring inside a longer string must NOT match,
    so a stray query parameter can't be misread as an ID."""
    assert parse_playlist_url("37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"


def test_rejects_empty_input():
    assert parse_playlist_url("") is None
    assert parse_playlist_url("   ") is None


def test_rejects_non_playlist_url():
    """User pastes the wrong Spotify URL (track, album, artist) — we
    should not produce a confident wrong ID."""
    assert (
        parse_playlist_url(
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        )
        is None
    )
    assert (
        parse_playlist_url(
            "https://open.spotify.com/album/2nL5UbqaDH3pXEvL3vG6kK"
        )
        is None
    )


def test_rejects_garbage():
    assert parse_playlist_url("hello world") is None
    assert parse_playlist_url("https://example.com/playlist/abc") is None


def test_strips_whitespace():
    """Real-world paste often includes a trailing newline or leading space."""
    url = "  https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M  \n"
    assert parse_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"


# ── extract_artists_from_tracks ─────────────────────────────────────────


def test_extracts_artists_from_tracks_response():
    """Pulls artist names from the standard playlist-tracks payload."""
    payload = {
        "items": [
            {"track": {"artists": [{"name": "Burial"}]}},
            {"track": {"artists": [{"name": "Four Tet"}]}},
            {"track": {"artists": [{"name": "Built to Spill"}]}},
        ]
    }
    assert extract_artists_from_tracks(payload) == [
        "Burial", "Four Tet", "Built to Spill"
    ]


def test_handles_top_level_tracks_wrapper():
    """The full playlist response wraps items inside `tracks.items`. The
    helper accepts either shape so callers can pass whichever they have."""
    payload = {
        "tracks": {
            "items": [
                {"track": {"artists": [{"name": "Burial"}]}},
            ]
        }
    }
    assert extract_artists_from_tracks(payload) == ["Burial"]


def test_dedupes_case_insensitively():
    """`Burial` and `burial` should count once. Preserves the
    first-appearance casing for display."""
    payload = {
        "items": [
            {"track": {"artists": [{"name": "Burial"}]}},
            {"track": {"artists": [{"name": "burial"}]}},  # dup
            {"track": {"artists": [{"name": "Four Tet"}]}},
        ]
    }
    assert extract_artists_from_tracks(payload) == ["Burial", "Four Tet"]


def test_preserves_order_of_first_appearance():
    """User's most-listened artists tend to sit at playlist heads —
    head-of-list order encodes taste priority. Don't sort."""
    payload = {
        "items": [
            {"track": {"artists": [{"name": "C"}]}},
            {"track": {"artists": [{"name": "A"}]}},
            {"track": {"artists": [{"name": "B"}]}},
            {"track": {"artists": [{"name": "A"}]}},  # dup
        ]
    }
    assert extract_artists_from_tracks(payload) == ["C", "A", "B"]


def test_handles_multi_artist_tracks():
    """Collabs / features list multiple artists per track. Each gets
    a chance to enter the unique list."""
    payload = {
        "items": [
            {
                "track": {
                    "artists": [{"name": "Burial"}, {"name": "Four Tet"}],
                }
            },
        ]
    }
    assert extract_artists_from_tracks(payload) == ["Burial", "Four Tet"]


def test_skips_unavailable_tracks():
    """Region-locked / deleted tracks have `track: null`. Don't crash."""
    payload = {
        "items": [
            {"track": None},
            {"track": {"artists": [{"name": "Burial"}]}},
            {"track": None},
        ]
    }
    assert extract_artists_from_tracks(payload) == ["Burial"]


def test_skips_empty_artist_names():
    """Defensive: if Spotify returns `name: ""` or missing, skip it
    rather than emitting blank artists into the textarea."""
    payload = {
        "items": [
            {"track": {"artists": [{"name": ""}]}},
            {"track": {"artists": [{}]}},
            {"track": {"artists": [{"name": "Burial"}]}},
        ]
    }
    assert extract_artists_from_tracks(payload) == ["Burial"]


def test_caps_at_max_artists():
    """A 200-track playlist with all-unique artists must produce at most
    MAX_ARTISTS — keeps the onboarding form within its own validation."""
    payload = {
        "items": [
            {"track": {"artists": [{"name": f"Artist {i}"}]}}
            for i in range(100)
        ]
    }
    out = extract_artists_from_tracks(payload)
    assert len(out) == MAX_ARTISTS


def test_returns_empty_for_empty_payload():
    assert extract_artists_from_tracks({}) == []
    assert extract_artists_from_tracks({"items": []}) == []
    assert extract_artists_from_tracks({"tracks": {"items": []}}) == []


# ── fetch_playlist_artists (short-circuit on no credentials) ───────────


def test_fetch_returns_empty_when_credentials_unset(monkeypatch):
    """When SPOTIFY_CLIENT_ID/SECRET are blank the token fetcher returns
    None and we should fall through to empty. The onboarding form treats
    empty as 'couldn't load' and lets the user enter artists manually."""
    from agent.config import settings as s
    monkeypatch.setattr(s, "spotify_client_id", "")
    monkeypatch.setattr(s, "spotify_client_secret", "")

    result = asyncio.run(fetch_playlist_artists("37i9dQZF1DXcBWIGoYBM5M"))
    assert result == []
