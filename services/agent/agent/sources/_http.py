"""HTTP client wrapper with browser TLS fingerprint impersonation.

Most sources work fine with plain httpx, but six Cloudflare-fronted publishers
(Boomkat, Norman Records, Quietus, Tone Glow, Drowned in Sound, Headphone
Commute) do JA3/JA4 TLS fingerprinting and 403 anything that doesn't look like
a real browser at the network layer — no User-Agent string fixes that, because
the block happens before the HTTP request is even parsed.

curl_cffi wraps curl-impersonate, which reproduces Chrome's TLS ClientHello
exactly (cipher suite order, ALPN, extensions, GREASE values, etc.). We route
all source crawls through it for consistency. Slight latency cost (~10-20ms
per request from the native handshake) is negligible for a weekly batch.

Why one shared helper: when curl_cffi releases a Chrome bump (e.g. chrome120 →
chrome125), only this file changes. The scrapers don't care which version
they're impersonating, just that the session does.
"""
from __future__ import annotations

from curl_cffi.requests import AsyncSession

# Matches the User-Agent we set in config.crawler_user_agent. Keep these in
# sync — sites that compare UA against TLS fingerprint will 403 a mismatch.
# chrome131 is the freshest fingerprint shipped with curl_cffi 0.10.x — newer
# than the chrome120 default, so less likely to be in stale scraper-detection
# fingerprint databases.
IMPERSONATE = "chrome131"

__all__ = ["AsyncSession", "IMPERSONATE"]
