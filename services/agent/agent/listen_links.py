"""Derive the ordered list of platform-link entries shown to readers
on a single record.

For every record we may have up to 5 Listen URLs — Bandcamp, Spotify,
Apple Music, YouTube, SoundCloud — derived during enrich_metadata and
extract_media_urls. The renderers (email + web) used to collapse this
into a single "Listen ↗" button picking whichever was highest in the
editorial preference order. That worked when most records had only one
listen URL; now that a typical record has 2-3, single-winner discards
real signal — some readers prefer Apple over Spotify, some go straight
to Bandcamp to support the artist.

This module converts the per-platform URL fields into the ordered list
the renderers iterate over. Order is the same editorial preference the
single-winner chain encoded — Bandcamp first (pays artists), Spotify
second (popular default), Apple third (alternative streaming), YouTube
fourth (broadly accessible), SoundCloud last (niche).
"""
from __future__ import annotations

from typing import Any

# Platform display label + the key on the record dict where its URL
# lives. Order is THE editorial preference order — earlier wins for the
# "primary" slot and is listed first in the UI strip.
_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("Bandcamp", "bandcamp_url"),
    ("Spotify", "spotify_url"),
    ("Apple Music", "apple_music_url"),
    ("YouTube", "youtube_url"),
    ("SoundCloud", "soundcloud_url"),
)


def build_listen_links(record: dict[str, Any]) -> list[dict[str, str]]:
    """Return the ordered list of {platform, url} entries the renderers
    should show for one record.

    Skips platforms with no URL. Returns an empty list when the record
    has no listen URLs at all — renderers omit the strip entirely in
    that case.
    """
    out: list[dict[str, str]] = []
    for label, key in _PLATFORMS:
        url = record.get(key)
        if url and isinstance(url, str) and url.strip():
            out.append({"platform": label, "url": url})
    return out


def primary_listen_url(record: dict[str, Any]) -> str | None:
    """The single URL we'd surface if forced to pick one (back-compat).

    Used by paths that haven't migrated to the strip yet — currently
    none, but the function provides a stable single-value contract."""
    links = build_listen_links(record)
    return links[0]["url"] if links else None
