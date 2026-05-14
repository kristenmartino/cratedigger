"""Tests for the article-media URL extractor.

`parse_embedded_media` is the pure function — it's what we test. The
network-fetching wrapper `extract_media_urls` is exercised in production;
mocking httpx-style sessions for unit tests is more setup than the value
warrants.

Coverage targets the regex behavior:
  - canonical URL shapes for all four platforms
  - rejection of look-alike strings that aren't real URLs
  - tolerance of the surrounding HTML/text containing other URLs
  - empty / None input
"""
from __future__ import annotations

from agent.article_media import parse_embedded_media


# ── Happy paths: each platform's canonical URL shape ───────────────────


def test_extracts_bandcamp_album_url():
    html = '<a href="https://burial.bandcamp.com/album/untrue">listen</a>'
    out = parse_embedded_media(html)
    assert out["bandcamp_url"] == "https://burial.bandcamp.com/album/untrue"


def test_extracts_bandcamp_track_url():
    html = '<iframe src="https://hyperdub.bandcamp.com/track/archangel"></iframe>'
    out = parse_embedded_media(html)
    assert out["bandcamp_url"] == "https://hyperdub.bandcamp.com/track/archangel"


def test_extracts_spotify_album_url():
    html = '<a href="https://open.spotify.com/album/1Acj4OcKWcVOIPSQMTH7Hl">Listen</a>'
    out = parse_embedded_media(html)
    assert out["spotify_url"] == "https://open.spotify.com/album/1Acj4OcKWcVOIPSQMTH7Hl"


def test_extracts_spotify_track_url():
    html = '<iframe src="https://open.spotify.com/track/2hjPpcdiKL3vL2yC1lo7g6"></iframe>'
    out = parse_embedded_media(html)
    assert out["spotify_url"] == "https://open.spotify.com/track/2hjPpcdiKL3vL2yC1lo7g6"


def test_extracts_youtube_watch_url():
    html = '<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">video</a>'
    out = parse_embedded_media(html)
    assert out["youtube_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_extracts_youtube_embed_url():
    html = '<iframe src="https://youtube.com/embed/dQw4w9WgXcQ"></iframe>'
    out = parse_embedded_media(html)
    assert out["youtube_url"] == "https://youtube.com/embed/dQw4w9WgXcQ"


def test_extracts_youtube_short_url():
    html = '<a href="https://youtu.be/dQw4w9WgXcQ">watch</a>'
    out = parse_embedded_media(html)
    assert out["youtube_url"] == "https://youtu.be/dQw4w9WgXcQ"


def test_extracts_soundcloud_track_url():
    html = '<a href="https://soundcloud.com/burial/archangel">listen</a>'
    out = parse_embedded_media(html)
    assert out["soundcloud_url"] == "https://soundcloud.com/burial/archangel"


# ── Mixed: multiple platforms in the same article ──────────────────────


def test_extracts_all_four_when_present():
    """Real article structure: Bandcamp iframe at top, Spotify embed in
    body, YouTube link to a session, SoundCloud demo embedded too."""
    html = """
    <article>
      <iframe src="https://burial.bandcamp.com/album/untrue"></iframe>
      <p>also on <a href="https://open.spotify.com/album/abc123">Spotify</a></p>
      <p>session: <a href="https://youtu.be/xyz789">YT</a></p>
      <p>demo: <a href="https://soundcloud.com/burial/demo-1">SC</a></p>
    </article>
    """
    out = parse_embedded_media(html)
    assert out["bandcamp_url"] == "https://burial.bandcamp.com/album/untrue"
    assert out["spotify_url"] == "https://open.spotify.com/album/abc123"
    assert out["youtube_url"] == "https://youtu.be/xyz789"
    assert out["soundcloud_url"] == "https://soundcloud.com/burial/demo-1"


def test_returns_first_match_when_multiple_per_platform():
    """If an article embeds the album AND a track from it, take the first
    (which is typically the album per editorial convention)."""
    html = """
    <iframe src="https://burial.bandcamp.com/album/untrue"></iframe>
    <iframe src="https://burial.bandcamp.com/track/archangel"></iframe>
    """
    out = parse_embedded_media(html)
    assert out["bandcamp_url"] == "https://burial.bandcamp.com/album/untrue"


# ── Rejections: things that look like URLs but aren't real ─────────────


def test_rejects_bandcamp_subdomain_without_album_or_track_path():
    """Bare label-page URLs (label.bandcamp.com without /album/X or /track/X)
    aren't release-specific listen targets — don't surface."""
    html = '<a href="https://hyperdub.bandcamp.com">label page</a>'
    out = parse_embedded_media(html)
    assert out["bandcamp_url"] is None


def test_rejects_spotify_invalid_path():
    """/artist/ pages aren't release listen targets; /show/ is podcasts."""
    html = '<a href="https://open.spotify.com/artist/abc123">Burial on Spotify</a>'
    out = parse_embedded_media(html)
    assert out["spotify_url"] is None


def test_rejects_youtube_channel_url():
    """/channel/ and /c/ pages aren't single-video listen targets."""
    html = '<a href="https://www.youtube.com/channel/UCxyz">channel</a>'
    out = parse_embedded_media(html)
    assert out["youtube_url"] is None


# ── Edge cases ─────────────────────────────────────────────────────────


def test_empty_input_returns_all_none():
    out = parse_embedded_media("")
    assert out == {
        "bandcamp_url": None,
        "spotify_url": None,
        "youtube_url": None,
        "soundcloud_url": None,
    }


def test_none_input_returns_all_none():
    out = parse_embedded_media(None)  # type: ignore[arg-type]
    assert out == {
        "bandcamp_url": None,
        "spotify_url": None,
        "youtube_url": None,
        "soundcloud_url": None,
    }


def test_html_without_any_media_links_returns_all_none():
    html = "<p>This article has no embeds, just text about Burial.</p>"
    out = parse_embedded_media(html)
    assert all(v is None for v in out.values())


def test_ignores_other_domains_that_might_look_similar():
    """A vanity domain like bandcamp.kristenmartino.ai (made up) or a
    string containing 'spotify' shouldn't false-positive."""
    html = """
    <p>not-bandcamp: bandcamp.example.com/foo</p>
    <p>not-spotify: blog.example.com/spotify-review</p>
    """
    out = parse_embedded_media(html)
    assert out["bandcamp_url"] is None
    assert out["spotify_url"] is None


def test_preserves_url_query_strings():
    """YouTube URLs commonly have ?v=ID and we want the whole thing."""
    html = '<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">video</a>'
    out = parse_embedded_media(html)
    assert out["youtube_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_case_insensitive_bandcamp_match():
    """Some HTML serializers uppercase href values. Match either case."""
    html = '<a href="HTTPS://BURIAL.BANDCAMP.COM/ALBUM/UNTRUE">listen</a>'
    out = parse_embedded_media(html)
    # Returns whatever case the source had — downstream code should
    # treat URLs case-insensitively on the host portion.
    assert out["bandcamp_url"] is not None
    assert "bandcamp.com" in out["bandcamp_url"].lower()
