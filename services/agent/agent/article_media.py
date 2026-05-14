"""Extract Bandcamp / Spotify / YouTube / SoundCloud URLs from source articles.

When a release comes from an RSS source, we have its `source.url` (the
article page) but not the actual listen URL of the record. Many editorial
sources embed iframes / anchor tags pointing at Bandcamp / Spotify /
YouTube / SoundCloud directly in the article — they're literally how
the article is written.

This module fetches the article HTML and pulls out those URLs. Higher
signal-to-noise than catalog search: the article IS about the record,
so embedded media is the canonical listen target. No fuzzy artist-match
verification needed (which is what protects us from the Dollar Diamonds
→ Doo Wop Volume 12 class of bug).

Per-platform regex matches are deliberately conservative — strict
character classes, no greedy patterns, no nested capture groups. If a
new platform domain shows up that we don't recognize, it gets ignored
rather than mis-matched.

Per-source fragility:
  - We're not trying to extract release metadata or play counts. Just
    URLs that match the four canonical hostnames. The patterns are
    domain-anchored so an article that mentions "youtube.com" in body
    text without an actual link won't false-positive.
  - On fetch failure (timeout, 4xx, 5xx) we return the all-None shape.
    The downstream enrich_metadata node's MB/Spotify-search fallback
    picks up the slack.
  - On HTML parse failure, regex still runs over the raw text. Even if
    the HTML structure changes, the matches survive as long as the
    URLs are present somewhere in the response body.

Used by extract_media_urls_node between normalize_releases and
enrich_metadata. Skipped per-release when the relevant columns are
already populated (caching: one fetch per release, ever).
"""
from __future__ import annotations

import logging
import re

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession

logger = logging.getLogger("cratedigger-agent.article_media")


# Per-platform URL patterns. Domain-anchored, no nested capture groups,
# allow the standard URL chars in the path. Stop at quote / whitespace /
# closing paren so we don't grab adjacent text.
#
# Bandcamp: <subdomain>.bandcamp.com/album/<slug> or /track/<slug>
# Spotify:  open.spotify.com/<album|track|playlist>/<id>
# YouTube:  youtube.com/watch?v=<id> or youtu.be/<id> or youtube.com/embed/<id>
# SoundCloud: soundcloud.com/<user>/<track>
_BANDCAMP_RE = re.compile(
    r'(https?://[a-z0-9-]+\.bandcamp\.com/(?:album|track)/[^\s"\'<>)]+)',
    re.IGNORECASE,
)
_SPOTIFY_RE = re.compile(
    r'(https?://open\.spotify\.com/(?:album|track|playlist)/[A-Za-z0-9]+)',
)
_YOUTUBE_RE = re.compile(
    r'(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)[A-Za-z0-9_-]+)',
)
_SOUNDCLOUD_RE = re.compile(
    r'(https?://soundcloud\.com/[a-z0-9_-]+/[a-z0-9_-]+)',
    re.IGNORECASE,
)


def parse_embedded_media(html: str) -> dict[str, str | None]:
    """Run the four regexes over arbitrary HTML/text, return first match per
    platform. Pure function; safe to test without network.

    Returns dict with keys: bandcamp_url, spotify_url, youtube_url,
    soundcloud_url. Each is a real URL string or None.
    """
    out: dict[str, str | None] = {
        "bandcamp_url": None,
        "spotify_url": None,
        "youtube_url": None,
        "soundcloud_url": None,
    }
    if not html:
        return out

    if m := _BANDCAMP_RE.search(html):
        out["bandcamp_url"] = m.group(1)
    if m := _SPOTIFY_RE.search(html):
        out["spotify_url"] = m.group(1)
    if m := _YOUTUBE_RE.search(html):
        out["youtube_url"] = m.group(1)
    if m := _SOUNDCLOUD_RE.search(html):
        out["soundcloud_url"] = m.group(1)
    return out


async def extract_media_urls(article_url: str) -> dict[str, str | None]:
    """Fetch the article at `article_url`, parse for embedded media URLs.

    Returns the same shape as parse_embedded_media. Empty (all-None)
    result on any HTTP error — the downstream enrich_metadata node's
    fallback chain (MB → Spotify search → Discogs) picks up the slack.

    Uses the existing curl_cffi session — same Chrome impersonation as
    the source crawlers, so article hosts that gate on TLS fingerprint
    (some Cloudflare-fronted publications) still respond.
    """
    empty = {
        "bandcamp_url": None,
        "spotify_url": None,
        "youtube_url": None,
        "soundcloud_url": None,
    }
    if not article_url:
        return empty

    headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with AsyncSession(
            timeout=15.0, headers=headers, impersonate=IMPERSONATE
        ) as http:
            resp = await http.get(article_url, allow_redirects=True)
            if resp.status_code != 200:
                logger.info(
                    "article_media: %s returned %d", article_url, resp.status_code,
                )
                return empty
            html = resp.text
    except Exception as e:
        logger.warning("article_media: fetch %s raised: %s", article_url, e)
        return empty

    found = parse_embedded_media(html)
    n_found = sum(1 for v in found.values() if v)
    if n_found:
        logger.info(
            "article_media: %d media URLs found in %s",
            n_found, article_url,
        )
    return found
