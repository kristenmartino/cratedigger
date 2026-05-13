"""Tests for the metadata-enrichment helpers.

The HTTP-layer calls (MusicBrainz, Cover Art Archive, Discogs) aren't
exercised here — those are integration concerns that get real coverage
when the pipeline runs against Neon. What we test:

  - The deterministic parsers (`_parse_mb_urls`, `_mb_cover_url`) given
    representative MB API response shapes.
  - The top-level `lookup_release` empty-input contract.

The HTTP throttle is a runtime-correctness concern (we have to honor MB's
1 req/sec) but a flaky thing to unit-test. We trust the time.monotonic()
math and the asyncio.Lock semantics.
"""
from __future__ import annotations

import asyncio

from agent.sources.metadata import (
    _mb_cover_url,
    _parse_mb_urls,
    lookup_release,
)


# ── _parse_mb_urls ──────────────────────────────────────────────────────


def test_parses_bandcamp_and_spotify_urls_from_relations():
    rg = {
        "relations": [
            {"type": "free streaming", "url": {"resource": "https://burial.bandcamp.com/album/untrue"}},
            {"type": "streaming", "url": {"resource": "https://open.spotify.com/album/abc123"}},
            {"type": "discogs", "url": {"resource": "https://www.discogs.com/release/12345"}},
        ]
    }
    bc, sp = _parse_mb_urls(rg)
    assert bc == "https://burial.bandcamp.com/album/untrue"
    assert sp == "https://open.spotify.com/album/abc123"


def test_parse_mb_urls_handles_missing_relations():
    bc, sp = _parse_mb_urls({})
    assert bc is None
    assert sp is None
    bc, sp = _parse_mb_urls({"relations": []})
    assert bc is None
    assert sp is None


def test_parse_mb_urls_first_bandcamp_wins():
    """Multiple Bandcamp links can exist (label page + release page). The
    parser takes the first to keep behavior deterministic; future tuning
    can prefer the release-page form."""
    rg = {
        "relations": [
            {"type": "discography", "url": {"resource": "https://hyperdub.bandcamp.com"}},
            {"type": "free streaming", "url": {"resource": "https://burial.bandcamp.com/album/untrue"}},
        ]
    }
    bc, _ = _parse_mb_urls(rg)
    assert bc == "https://hyperdub.bandcamp.com"


def test_parse_mb_urls_ignores_unrelated_resources():
    rg = {
        "relations": [
            {"type": "wikipedia", "url": {"resource": "https://en.wikipedia.org/wiki/Burial"}},
            {"type": "official homepage", "url": {"resource": "https://burial.com"}},
        ]
    }
    bc, sp = _parse_mb_urls(rg)
    assert bc is None
    assert sp is None


def test_parse_mb_urls_tolerates_malformed_entries():
    """An entry with no `url` key, or `url` set to None, or `resource`
    missing — none of these should raise."""
    rg = {
        "relations": [
            {"type": "free streaming"},  # no url key
            {"type": "free streaming", "url": None},  # null url
            {"type": "free streaming", "url": {}},  # url without resource
            {"type": "free streaming", "url": {"resource": "https://burial.bandcamp.com"}},
        ]
    }
    bc, _ = _parse_mb_urls(rg)
    assert bc == "https://burial.bandcamp.com"


# ── _mb_cover_url ───────────────────────────────────────────────────────


def test_cover_url_when_caa_count_positive():
    rg = {"cover-art-archive": {"count": 3, "front": True}}
    url = _mb_cover_url("a-mbid", rg)
    assert url == "https://coverartarchive.org/release-group/a-mbid/front-500"


def test_cover_url_none_when_caa_count_zero():
    rg = {"cover-art-archive": {"count": 0}}
    assert _mb_cover_url("a-mbid", rg) is None


def test_cover_url_none_when_caa_block_missing():
    """Older or sparse MB rows may not include the cover-art-archive block
    at all — treat as no cover."""
    assert _mb_cover_url("a-mbid", {}) is None


def test_cover_url_handles_string_count():
    """Some MB endpoints serialize the count as a string. Both forms
    should produce a valid URL."""
    rg = {"cover-art-archive": {"count": "2"}}
    assert _mb_cover_url("a-mbid", rg) == "https://coverartarchive.org/release-group/a-mbid/front-500"


# ── lookup_release (empty-input contract) ──────────────────────────────


def test_lookup_release_empty_inputs_short_circuit():
    """Empty artist or title must NOT make any network calls. Returns the
    all-null shape immediately. Same input-validation pattern as
    extract_releases and send_failure_alert."""
    out = asyncio.run(lookup_release("", "Some Title"))
    assert out == {
        "cover_art_url": None,
        "bandcamp_url": None,
        "spotify_url": None,
        "mbid": None,
    }

    out = asyncio.run(lookup_release("Some Artist", ""))
    assert out["mbid"] is None
    assert out["cover_art_url"] is None
