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
    _artists_match,
    _mb_artist_credit_name,
    _mb_cover_url,
    _normalize_artist,
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


# ── Artist match verification ───────────────────────────────────────────


def test_normalize_artist_strips_case_punct_and_accents():
    assert _normalize_artist("Mary Yuzovskaya") == "mary yuzovskaya"
    assert _normalize_artist("MARY YUZOVSKAYA") == "mary yuzovskaya"
    assert _normalize_artist("Mary  Yuzovskaya!") == "mary yuzovskaya"
    assert _normalize_artist("Caterina Barbieri") == "caterina barbieri"
    # ASCII-fold
    assert _normalize_artist("Sigur Rós") == "sigur ros"


def test_normalize_artist_empty():
    assert _normalize_artist("") == ""
    assert _normalize_artist(None) == ""  # type: ignore[arg-type]


def test_artists_match_exact_after_normalization():
    assert _artists_match("Burial", "BURIAL") is True
    assert _artists_match("Boards Of Canada", "Boards of Canada") is True
    assert _artists_match("Loraine James", "loraine james") is True


def test_artists_match_whole_word_substring():
    """Label-prefix variants like 'Various Artists - Hyperdub' against
    'Hyperdub' should match — 'hyperdub' appears as a whole word in the
    longer normalized string."""
    assert _artists_match("Hyperdub", "Various Artists - Hyperdub") is True


def test_artists_match_feature_credits_via_substring():
    """'Burial' matches 'Burial Four Tet' (after punctuation strip the
    join 'Burial & Four Tet' becomes 'burial four tet', and 'burial' is
    a whole-word prefix)."""
    assert _artists_match("Burial", "Burial & Four Tet") is True
    # Order also OK — 'burial' is still a whole word in the longer string
    assert _artists_match("Burial", "Four Tet & Burial") is True


def test_artists_match_rejects_unrelated():
    """The actual production bug: a Dollar Diamonds release matched to a
    doo-wop compilation. Verification must reject this."""
    assert _artists_match("Dollar Diamonds", "Various Artists") is False
    assert _artists_match("Dollar Diamonds", "The Drifters") is False
    # The Doo Wop compilation case specifically
    assert _artists_match(
        "Dollar Diamonds",
        "Various - Street Corner Symphonies Volume 12 1960",
    ) is False


def test_artists_match_rejects_empty():
    """Without artist info on either side, no match — better to skip
    enrichment than to claim a match without evidence."""
    assert _artists_match("Burial", "") is False
    assert _artists_match("", "Burial") is False
    assert _artists_match("", "") is False


def test_artists_match_rejects_common_short_words_as_substring():
    """'The Drifters' must not match 'The Beatles' just because 'the' is
    in both. The 4-char-minimum-on-shorter rule handles this — the only
    accepted match path for 'the' would be exact equality of the full
    string, which these aren't."""
    assert _artists_match("The Drifters", "The Beatles") is False
    # Even bare common words on one side never bridge a substring match
    assert _artists_match("the", "The Beatles") is False
    assert _artists_match("an", "An Album") is False


def test_artists_match_rejects_partial_word_match():
    """'Burial' must NOT match 'BurialGround' — substring without word
    boundary is a false positive."""
    assert _artists_match("Burial", "BurialGround") is False


def test_artists_match_rejects_single_letter_substrings():
    """'M' inside 'M Lamar' would substring-match without the min-length
    guard. The 4-char min rejects it. M83 / U2 / U.N.K.L.E. style short
    real artists are out of scope for substring matching — they have to
    self-match exactly. That's an acceptable coverage cost."""
    assert _artists_match("M", "M Lamar") is False
    assert _artists_match("U2", "U2 vs Brian Eno") is False


# ── _mb_artist_credit_name ──────────────────────────────────────────────


def test_mb_artist_credit_simple_single_artist():
    rg = {"artist-credit": [{"name": "Burial", "artist": {"name": "Burial"}}]}
    assert _mb_artist_credit_name(rg) == "Burial"


def test_mb_artist_credit_with_joinphrase():
    """Multi-artist credits use joinphrase to glue names together —
    e.g. 'Burial & Four Tet'."""
    rg = {
        "artist-credit": [
            {"name": "Burial", "joinphrase": " & "},
            {"name": "Four Tet"},
        ]
    }
    assert _mb_artist_credit_name(rg) == "Burial & Four Tet"


def test_mb_artist_credit_falls_back_to_artist_dict_name():
    """When the top-level `name` is missing, pull from the nested
    artist.name. Both fields appear in MB's response in different
    contexts."""
    rg = {"artist-credit": [{"artist": {"name": "Burial"}}]}
    assert _mb_artist_credit_name(rg) == "Burial"


def test_mb_artist_credit_empty():
    assert _mb_artist_credit_name({}) == ""
    assert _mb_artist_credit_name({"artist-credit": []}) == ""


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
