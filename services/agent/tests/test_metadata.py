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
    _APPLE_MUSIC_ID_RE,
    _BANDCAMP_OG_TITLE_RE,
    _SPOTIFY_ALBUM_ID_RE,
    _artists_match,
    _itunes_artwork_hires,
    _mb_artist_credit_name,
    _mb_cover_url,
    _normalize_artist,
    _parse_mb_urls,
    _spotify_album_artist_name,
    _youtube_candidate_artist,
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
        "apple_music_url": None,
        "youtube_url": None,
        "mbid": None,
    }

    out = asyncio.run(lookup_release("Some Artist", ""))
    assert out["mbid"] is None
    assert out["cover_art_url"] is None


# ── _spotify_album_artist_name ──────────────────────────────────────────


def test_spotify_artist_name_single():
    """Standard single-artist album response shape."""
    album = {
        "artists": [{"name": "Burial", "id": "abc"}],
        "name": "Untrue",
    }
    assert _spotify_album_artist_name(album) == "Burial"


def test_spotify_artist_name_collab():
    """Multi-artist albums (splits, collabs). Concatenated so the
    artist-match's whole-word substring rule fires correctly — input
    'Burial' against the concatenated 'Burial Four Tet' matches via
    substring, just like the MB collab case."""
    album = {
        "artists": [
            {"name": "Burial"},
            {"name": "Four Tet"},
        ],
        "name": "Moth / Wolf Cub",
    }
    assert _spotify_album_artist_name(album) == "Burial Four Tet"


def test_spotify_artist_name_empty():
    """Missing or empty artists block — no exception, returns empty string."""
    assert _spotify_album_artist_name({}) == ""
    assert _spotify_album_artist_name({"artists": []}) == ""


def test_spotify_artist_name_tolerates_missing_name_field():
    """A malformed artist entry without `name` shouldn't raise."""
    album = {
        "artists": [
            {"id": "abc"},  # no name
            {"name": "Burial"},
        ],
    }
    assert _spotify_album_artist_name(album) == "Burial"


# ── _youtube_candidate_artist ───────────────────────────────────────────


def test_youtube_artist_from_channel_only():
    """Many albums upload by an aggregator with the artist in the channel
    name and a track listing in the video title — the artist might appear
    only on the channel side."""
    snippet = {
        "channelTitle": "Burial",
        "title": "Untrue [Full Album]",
    }
    out = _youtube_candidate_artist(snippet)
    assert "Burial" in out
    assert "Untrue" in out


def test_youtube_artist_from_video_title_only():
    """Just as often, uploaded by a third-party channel ('AlbumUploads')
    with the artist embedded in the video title."""
    snippet = {
        "channelTitle": "Full Albums HD",
        "title": "Burial - Untrue (2007)",
    }
    out = _youtube_candidate_artist(snippet)
    assert "Burial" in out
    assert "Full Albums HD" in out


def test_youtube_artist_combines_both_sides():
    """The combined string is what _artists_match runs over — it has the
    whole-word substring rule so 'Burial' as a token in either side
    counts."""
    snippet = {
        "channelTitle": "Hyperdub Records",
        "title": "Burial — Untrue (Full Album)",
    }
    out = _youtube_candidate_artist(snippet)
    # Both halves present so the rule has the maximum signal.
    assert "Hyperdub" in out and "Burial" in out


def test_youtube_candidate_empty_when_snippet_blank():
    """No channel + no title → empty string (rather than 'None None')."""
    assert _youtube_candidate_artist({}) == ""
    assert _youtube_candidate_artist({"channelTitle": "", "title": ""}) == ""
    assert _youtube_candidate_artist({"channelTitle": None, "title": None}) == ""


# ── _itunes_artwork_hires ───────────────────────────────────────────────


def test_itunes_artwork_upgrades_100_to_600():
    """The CDN serves the same image at any size token. Standard
    iTunes Search responses come back at 100x100; bumping the path
    to 600x600 gets us a crisp cover with no extra request."""
    src = "https://is1-ssl.mzstatic.com/image/thumb/Music/abc123/cover.jpg/100x100bb.jpg"
    out = _itunes_artwork_hires(src)
    assert out == "https://is1-ssl.mzstatic.com/image/thumb/Music/abc123/cover.jpg/600x600bb.jpg"


def test_itunes_artwork_handles_non_bb_variant():
    """Some older response shapes use plain `100x100.jpg` without the
    `bb` quality token. The substitution still works."""
    src = "https://example.com/cover/100x100.jpg"
    out = _itunes_artwork_hires(src)
    assert out == "https://example.com/cover/600x600.jpg"


def test_itunes_artwork_handles_png():
    """Apple sometimes returns PNG covers for older releases."""
    src = "https://example.com/cover/100x100bb.png"
    out = _itunes_artwork_hires(src)
    assert out == "https://example.com/cover/600x600bb.png"


def test_itunes_artwork_passthrough_when_no_size_token():
    """If the URL doesn't end in a /<n>x<n>.(jpg|png) the regex
    misses and we return the input unchanged — never raise."""
    src = "https://example.com/some/other/url"
    assert _itunes_artwork_hires(src) == src


# ── URL verification regex extractors ───────────────────────────────────
#
# The verify_* functions are HTTP-bound and would require live API mocking
# to test end-to-end; the regex extractors that pull the platform-specific
# IDs out of a stored URL are pure and worth pinning.


def test_spotify_album_id_extracts_from_standard_url():
    """Standard share URL — 22-char base62 ID."""
    m = _SPOTIFY_ALBUM_ID_RE.search(
        "https://open.spotify.com/album/0I4OoU70unqcUu7G2iqAjK"
    )
    assert m and m.group(1) == "0I4OoU70unqcUu7G2iqAjK"


def test_spotify_album_id_extracts_with_si_param():
    m = _SPOTIFY_ALBUM_ID_RE.search(
        "https://open.spotify.com/album/0I4OoU70unqcUu7G2iqAjK?si=abc123"
    )
    assert m and m.group(1) == "0I4OoU70unqcUu7G2iqAjK"


def test_spotify_album_id_rejects_track_url():
    """Track URLs and playlist URLs must not be misread as albums."""
    assert (
        _SPOTIFY_ALBUM_ID_RE.search(
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        )
        is None
    )
    assert (
        _SPOTIFY_ALBUM_ID_RE.search(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        is None
    )


def test_apple_music_id_extracts_from_standard_url():
    """music.apple.com/us/album/<slug>/<numeric-id>"""
    m = _APPLE_MUSIC_ID_RE.search(
        "https://music.apple.com/us/album/setting/1234567890"
    )
    assert m and m.group(1) == "1234567890"


def test_apple_music_id_extracts_from_other_storefront():
    """Storefront codes vary (us, gb, de, jp …). Pattern accepts any."""
    m = _APPLE_MUSIC_ID_RE.search(
        "https://music.apple.com/jp/album/setting/9876543210"
    )
    assert m and m.group(1) == "9876543210"


def test_apple_music_id_rejects_artist_url():
    """An artist-page URL (no /album/<slug>/<id> shape) doesn't match."""
    assert (
        _APPLE_MUSIC_ID_RE.search(
            "https://music.apple.com/us/artist/setting/1234567890"
        )
        is None
    )


def test_bandcamp_og_title_extracts_artist():
    """og:title pattern: 'Album Title, by Artist Name'. The verifier
    splits on the last ', by ' (some album titles contain commas)."""
    html = (
        '<html><head>'
        '<meta property="og:title" content="Setting, by Setting">'
        '</head></html>'
    )
    m = _BANDCAMP_OG_TITLE_RE.search(html)
    assert m
    og_title = m.group(1)
    assert ", by " in og_title
    candidate = og_title.rsplit(", by ", 1)[-1]
    assert candidate == "Setting"


def test_bandcamp_og_title_handles_comma_in_album_name():
    """Album titles with commas: 'Sing, Memory, by Burial' should parse
    artist as 'Burial', not 'Memory'."""
    html = '<meta property="og:title" content="Sing, Memory, by Burial">'
    m = _BANDCAMP_OG_TITLE_RE.search(html)
    assert m
    candidate = m.group(1).rsplit(", by ", 1)[-1]
    assert candidate == "Burial"


def test_bandcamp_og_title_returns_none_on_missing_meta():
    """A page without og:title (rare but possible for older Bandcamp
    pages or non-standard layouts) — the verifier preserves the URL
    rather than dropping."""
    html = "<html><head><title>Some Page</title></head></html>"
    assert _BANDCAMP_OG_TITLE_RE.search(html) is None
