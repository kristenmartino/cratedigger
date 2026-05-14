"""Tests for build_listen_links — the helper that the email + web
renderers use to produce the per-record platform-link strip.

This is THE editorial-order contract: changes here move the order
buttons appear in inboxes. Pin it tightly.
"""
from __future__ import annotations

from agent.listen_links import build_listen_links, primary_listen_url


def test_returns_empty_when_no_urls():
    assert build_listen_links({}) == []
    assert build_listen_links({"bandcamp_url": None, "spotify_url": None}) == []


def test_single_platform_returns_single_entry():
    out = build_listen_links({"bandcamp_url": "https://example.bandcamp.com"})
    assert out == [{"platform": "Bandcamp", "url": "https://example.bandcamp.com"}]


def test_editorial_order_bandcamp_first():
    """Bandcamp pays artists, so it's the canonical first link."""
    out = build_listen_links({
        "spotify_url": "https://open.spotify.com/album/abc",
        "bandcamp_url": "https://burial.bandcamp.com/album/untrue",
        "apple_music_url": "https://music.apple.com/album/xyz",
    })
    assert [link["platform"] for link in out] == [
        "Bandcamp", "Spotify", "Apple Music"
    ]


def test_full_order_all_five_platforms():
    """When every platform is present, the strip surfaces all five
    in the canonical Bandcamp → Spotify → Apple → YouTube → SoundCloud
    order. Changes to this assertion are intentional editorial moves."""
    out = build_listen_links({
        "bandcamp_url": "https://a.bandcamp.com",
        "spotify_url": "https://open.spotify.com/album/b",
        "apple_music_url": "https://music.apple.com/album/c",
        "youtube_url": "https://www.youtube.com/watch?v=d",
        "soundcloud_url": "https://soundcloud.com/e",
    })
    assert [link["platform"] for link in out] == [
        "Bandcamp", "Spotify", "Apple Music", "YouTube", "SoundCloud"
    ]


def test_skips_empty_strings():
    """Postgres can return empty-string columns (vs NULL) depending on
    upstream defaults. Skip those — an anchor to an empty URL is worse
    than no anchor."""
    out = build_listen_links({
        "bandcamp_url": "",
        "spotify_url": "   ",
        "apple_music_url": "https://music.apple.com/album/xyz",
    })
    assert len(out) == 1
    assert out[0]["platform"] == "Apple Music"


def test_skips_non_string_values():
    """Defensive: if a row comes back with an unexpected type (e.g.
    None, int) we shouldn't render it. Empty list is fine."""
    out = build_listen_links({
        "bandcamp_url": None,
        "spotify_url": 0,  # type: ignore[arg-type]
        "youtube_url": "https://www.youtube.com/watch?v=abc",
    })
    assert [link["platform"] for link in out] == ["YouTube"]


def test_primary_listen_url_matches_first_link():
    """The back-compat single-URL helper just returns the first link's
    URL — same as the renderer's primary slot."""
    record = {
        "spotify_url": "https://open.spotify.com/album/abc",
        "bandcamp_url": "https://burial.bandcamp.com/album/untrue",
    }
    assert primary_listen_url(record) == "https://burial.bandcamp.com/album/untrue"


def test_primary_listen_url_none_when_no_urls():
    assert primary_listen_url({}) is None
