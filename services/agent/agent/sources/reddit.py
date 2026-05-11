"""Reddit subreddit ingest via the "script app" OAuth flow.

Reddit's public RSS endpoint is 403'd from datacenter IPs (GitHub Actions,
most cloud providers). The fix is the authenticated JSON API at
oauth.reddit.com — same data, allowed from any IP, free.

Auth flow:
  1. Create an app of type "script" at https://www.reddit.com/prefs/apps.
     You get a client_id (under the app name) and client_secret.
  2. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in the agent's env.
  3. We POST to /api/v1/access_token with HTTP Basic auth (client_id,
     client_secret) and grant_type=client_credentials. Reddit returns a
     bearer token good for ~1 hour, plenty for a single crawl run.
  4. Each subreddit GET sends the token as Authorization: Bearer.

source.ingest_url is the canonical subreddit URL (https://www.reddit.com/r/X)
— easy to read in sources.json. We parse the subreddit name out of it and
build the API URL ourselves.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from agent.config import settings
from agent.sources._http import IMPERSONATE, AsyncSession
from agent.sources.rss import RawRelease, parse_release_title

logger = logging.getLogger("cratedigger-agent.sources.reddit")

OAUTH_HOST = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
TIMEFRAME = "week"
LIMIT = 50

_SUBREDDIT_RE = re.compile(r"/r/([A-Za-z0-9_]+)")


async def _get_token(http: AsyncSession) -> str | None:
    if not (settings.reddit_client_id and settings.reddit_client_secret):
        logger.warning(
            "Reddit OAuth not configured — set REDDIT_CLIENT_ID / "
            "REDDIT_CLIENT_SECRET. Skipping reddit sources."
        )
        return None
    try:
        resp = await http.post(
            TOKEN_URL,
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            data={"grant_type": "client_credentials"},
            timeout=10.0,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            logger.error("Reddit token response missing access_token: %s", resp.text)
            return None
        return token
    except Exception as e:
        logger.error("Reddit token fetch failed: %s", e)
        return None


def _thumbnail_or_none(post: dict) -> str | None:
    """Reddit thumbnails can be 'self', 'default', 'nsfw', or an http URL.
    Only pass through the actual URLs; reject the placeholder strings."""
    t = post.get("thumbnail") or ""
    return t if t.startswith("http") else None


async def fetch_reddit_subreddit(slug: str, url: str) -> list[RawRelease]:
    """Fetch top-of-week posts from one subreddit. Returns RawReleases."""
    m = _SUBREDDIT_RE.search(url)
    if not m:
        logger.error("Reddit %s: couldn't extract subreddit from %r", slug, url)
        return []
    subreddit = m.group(1)

    base_headers = {"User-Agent": settings.crawler_user_agent}
    try:
        async with AsyncSession(
            timeout=20.0, headers=base_headers, impersonate=IMPERSONATE
        ) as http:
            token = await _get_token(http)
            if not token:
                return []

            api_url = f"{OAUTH_HOST}/r/{subreddit}/top?t={TIMEFRAME}&limit={LIMIT}"
            resp = await http.get(
                api_url,
                headers={**base_headers, "Authorization": f"Bearer {token}"},
                allow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Reddit %s fetch failed: %s", slug, e)
        return []

    out: list[RawRelease] = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = (post.get("title") or "").strip()
        if not title:
            continue

        # /r/listentothis enforces "Artist -- Title [Genre, Year]"; other
        # subreddits don't. parse_release_title handles both — unformatted
        # titles return ("", title), which the downstream pipeline can
        # filter or extract from later via LLM.
        artist, release_title = parse_release_title(title)

        permalink = post.get("permalink") or ""
        thread_url = "https://www.reddit.com" + permalink if permalink else url
        # Prefer the linked media URL when present (Bandcamp / YouTube / etc.)
        media_url = post.get("url_overridden_by_dest") or post.get("url") or thread_url

        created = post.get("created_utc")
        release_date = (
            datetime.fromtimestamp(float(created), tz=timezone.utc) if created else None
        )

        out.append(
            RawRelease(
                title=release_title,
                artist=artist,
                label=None,
                catalog_number=None,
                release_date=release_date,
                url=media_url,
                cover_art_url=_thumbnail_or_none(post),
                description=post.get("selftext") or title,
                source_slug=slug,
            )
        )

    logger.info("Reddit %s (r/%s): %d entries", slug, subreddit, len(out))
    return out
