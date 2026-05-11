# Sources

How the Crate Digger agent gets the raw release signal: what works, what doesn't, what we tried.

See `services/agent/data/sources.json` for the live roster — this doc explains the *why* behind it.

## Active sources (10)

| Source | Method | Notes |
|---|---|---|
| A Closer Listen | RSS | format: `Artist ~ Title` |
| Aquarium Drunkard | RSS | format: `Artist :: Title` |
| Bandcamp Daily | RSS | mixed: `Artist, "Title"` for release reviews; magazine headlines for features |
| Crack Magazine | RSS | mostly headlines (news/festival/announcement); LLM extraction expected |
| FACT Magazine | RSS | mostly headlines |
| Loud and Quiet | RSS | mixed: podcast titles use `—`; features are headlines |
| Pitchfork (Album Reviews) | RSS | headlines; artist usually in description body |
| Stereogum | RSS | mostly headlines |
| Hardwax | scrape | URL-path parser: `/{id}/{artist-slug}/{title-slug}/` |
| Resident Advisor | scrape | parses `__NEXT_DATA__` JSON blob |

All HTTP calls go through `curl_cffi` impersonating Chrome 131 (`agent/sources/_http.py`). See "TLS fingerprinting" below for why.

## Inactive sources (10)

Preserved in `sources.json` with URLs intact for future reactivation.

### Blocked by datacenter-IP detection (6)

All 4xx'd from GitHub Actions runners and (likely) Railway after every UA + TLS workaround. The block decision happens before TLS impersonation can help.

| Source | Method | Behavior |
|---|---|---|
| Boomkat | scrape | 403 on every UA + TLS profile |
| Norman Records | scrape | 403 on every UA + TLS profile |
| The Quietus | RSS | 403/404 — server returns different responses to fingerprint, all unhelpful |
| Tone Glow | RSS | 403 — Substack bot fight mode |
| Drowned in Sound | RSS | 403 — Substack bot fight mode |
| Headphone Commute | RSS | 403 |

**Reactivation path:** residential proxy (BrightData / ScraperAPI / Oxylabs, ~$50-100/mo). Wire one via an `HTTP_PROXY`-style env var; no code changes beyond plumbing.

### Other reasons (4)

| Source | Why |
|---|---|
| Bleep | `https://bleep.com/genre/all/new-releases` returns 404; URL needs a different shape |
| The Wire | Tried `/articles/feed/`, `/news/feed`, `/feed` — all 404. May not have a public RSS feed |
| Brainwashed | `/feed/` returns 404; no working URL found |
| Fluid Radio | Returns 10 entries but the **content is Spanish casino spam** — site has been hijacked or pivoted |

## What we tried (and the rabbit holes that didn't pan out)

### TLS fingerprinting → curl_cffi swap

After exhausting User-Agent variants (`Crate Digger Music Digest`, polite-bot `Mozilla/5.0 (compatible; CrateDigger; +mailto:...)`, real Chrome on macOS/Linux), 6 Cloudflare-fronted sources stayed at 403. The block happens at the TLS layer — Cloudflare's default WAF inspects the JA3/JA4 fingerprint of the ClientHello and rejects anything that doesn't match a real browser's handshake.

`curl_cffi` wraps `curl-impersonate`, which reproduces Chrome's TLS ClientHello exactly (cipher suite order, ALPN, extensions, GREASE values). We swapped every source-crawl HTTP call (`fetch_rss`, all scrapers) onto it. **Anthropic API calls keep plain `httpx`** — Anthropic doesn't fingerprint, and `curl_cffi` has slightly higher per-request overhead from the native handshake.

**Result:** confirmed `curl_cffi` works at the protocol level (error format changed from httpx's to curl-impersonate's; Quietus's response changed from `403` to `404`, meaning the WAF saw a different request shape). But it didn't unblock the 6 sources. We tried both `chrome120` and `chrome131` (the freshest Chrome target available in curl_cffi 0.10.x). No effect.

**Conclusion:** the block isn't TLS-based. The common factor across all 6 sources is the runner's source IP (GitHub-Actions datacenter range), which Cloudflare and Substack maintain block lists for. No amount of fingerprint impersonation gets past that.

### Reddit OAuth → tried, reverted

Reddit's public RSS endpoints (`/r/X/top.rss?t=week`) are 403'd from datacenter IPs. The "script app" OAuth flow (free, no rate-cost, `client_credentials` grant) was supposed to fix it — Reddit's authenticated API at `oauth.reddit.com` works from any IP.

Implemented `agent/sources/reddit.py` with token acquisition + per-subreddit fetch, wired `ingest_method="api"` dispatch, added env vars `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` and a 4-subreddit slate (`r/listentothis`, `r/electronicmusic`, `r/idm`, `r/ambientmusic`). Reverted after the app-creation flow hit a wall on the user's end — kept the codebase honest, dropped dead code.

If you ever circle back: the implementation is in commits `0d31534` (added) and `a1a5553` (reverted). The `ingest_method="api"` enum value still exists in `packages/db/init.sql` so reintroducing the flow is just code, not schema.

### Hardwax CSS-class drift → URL-path parser

The original Hardwax scraper used semantic-looking selectors (`li.record`, `.artist`, `.title`, etc.). When we finally crawled the live site, we discovered Hardwax now ships **minified CSS class names** (`ps`, `qz`, `rl`, `co`, `cq`) from a build process. Those class names rotate per deploy and are useless for parsing.

What's stable on every Hardwax page:
- Every record card is `<div id="record-NNNNN">` (numeric ID, `record-` prefix)
- Each card contains `<a class="an" href="/{record_id}/{artist-slug}/{title-slug}/">`

The current parser (`agent/sources/hardwax.py`) is URL-driven: find each `div[id^="record-"]`, read its `a.an` href, split the path. Artist + title slugs convert to display form via `_slug_to_display` (`mary-yuzovskaya` → `Mary Yuzovskaya`). Catalog-number-style slugs come back capitalized but are tolerable (`bin008` → `Bin008`); downstream metadata fetch can normalize.

### No-cards diagnostic helper

When a selector-based scraper gets 200 OK but finds zero records, `agent/sources/_debug.log_no_cards_diagnostic` dumps the page title, counts of common card-container tags, and the first 2KB of `<body>` HTML to the log. This is what surfaced Hardwax's minified-class problem in one run — no need to re-fetch and inspect HTML manually.

Wired into every selector-based scraper (Boomkat, Norman Records, Hardwax, Bleep). RA uses `__NEXT_DATA__` JSON parsing instead of selectors, so it doesn't need this.

### Title-extraction heuristic

`agent/sources/rss.parse_release_title()` handles the formatted feeds with a per-pattern separator list — `—` (em-dash), `–` (en-dash), `--` (double-hyphen, for r/listentothis style), `~`, `::`, `-` — plus a Bandcamp Daily quoted-title regex (`Artist, "Title"`). Order matters: the quoted-title regex runs first so headlines with an incidental comma (`...on Bandcamp, April 2026`) don't carve out a fake artist.

For news-style headlines (Pitchfork, Stereogum, FACT, Crack, most of L&Q), the heuristic returns `("", title)` — empty artist. Those get picked up by the LLM extraction step.

## LLM artist extraction

`agent/extract.py` runs Claude Haiku on every entry that comes out of `ingest_sources_node` with empty artist. Realtime API (not Batches), 25 entries per call, up to 8 calls in parallel via `asyncio.Semaphore`. ~$0.05 per crawl at current volumes.

Why this step exists: `normalize_releases_node` drops every entry with empty `artist_normalized` or `title_normalized` (silent loss). Without extraction, the ~150-200 magazine-headline entries from Pitchfork / Stereogum / FACT / Crack / Loud & Quiet are dropped before they reach scoring or dedup. With extraction, the LLM classifies each as release-vs-news and pulls (artist, title) for the releases.

Gated on `ANTHROPIC_API_KEY`. Without it, every entry is marked `is_release=False` (drop, don't fabricate). No network calls, no exception path.

## Operational notes

### Crawl-check workflow

`.github/workflows/crawl.yml` runs `scripts/crawl_check.py` on a GitHub-hosted runner. Triggers:

- `workflow_dispatch` with optional `slug` (one source) or `filter=rss-only|scrape-only`
- Path-trigger on changes to `sources/**`, `sources.json`, the crawl script, or the workflow itself

Output goes to `$GITHUB_STEP_SUMMARY`, so the per-source count table renders on the run page without scrolling logs. No DB / no LLM / no email — pure source-availability check.

This runs from a GitHub Actions datacenter IP. If a source works in this workflow, it'll work on Railway. The reverse isn't always true — some sources Railway might reach but the runner won't, but in practice we haven't seen that asymmetry.

### Politeness

Per `CLAUDE.md`, the crawler is non-aggressive:
- 1 request per source per crawl (we don't follow detail pages)
- Real Chrome User-Agent (`Mozilla/5.0 (Macintosh ... Chrome/131.0.0.0 ...`)
- robots.txt-respecting (httpx and curl_cffi both honor)
- 20-second timeout per request, fail-soft

If a site owner ever pings to ask about traffic: the volume from one crawl is 10 requests, weekly, totaling 290-ish releases ingested.

### Adding a new source

1. Add a row to `services/agent/data/sources.json` with `name`, `slug`, `ingest_method`, `ingest_url`, `default_weight`, `genre_affinity`, `active`.
2. If `ingest_method="rss"` — no code changes; `fetch_rss` picks it up automatically.
3. If `ingest_method="scrape"` — drop a module under `agent/sources/`, register it in `agent/sources/__init__.py`'s `SCRAPERS` dict.
4. If `ingest_method="api"` — wire a fetcher; see the reverted Reddit module in commit `0d31534` for the pattern (`API_FETCHERS` registry routes by URL host).
5. Push to a branch that touches `agent/sources/**` or `data/sources.json` to trigger the crawl-check workflow. Read the run summary to verify the new source returns data before merging.
