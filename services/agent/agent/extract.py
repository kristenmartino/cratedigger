"""LLM-driven (artist, title) extraction for magazine RSS feeds.

The release-shaped feeds (Bandcamp Daily, Aquarium Drunkard, A Closer Listen,
Hardwax, RA) put artist + title right in the entry title, so the
parse_release_title heuristic in agent/sources/rss.py handles them.

Magazine feeds (Pitchfork, Stereogum, FACT, Crack Magazine, parts of L&Q,
half of Bandcamp Daily) put article headlines there instead:

    "Spellbound Festival announces 2026 programme"   (news)
    "Now Kash Patel's Ripping Off The Beastie Boys"  (news)
    "Loud Bloom"                                     (release; artist in body)
    "Devon Turnbull's Ojas Music label launches with Muller and Totland's
     Unna"                                           (release announcement)

normalize_releases_node drops anything with an empty artist, so without
extraction every entry from those 5 feeds is silently discarded — that's
~200 entries per crawl. This module asks Claude Haiku to classify each
empty-artist entry as release-vs-news and pull (artist, title) for the
releases.

Realtime API (not Batches): the extraction needs to finish inside one
workflow node so dedup downstream can use the enriched data. Cost at our
scale is negligible (~$0.05 per crawl, weekly).
"""
from __future__ import annotations

import asyncio
import json
import logging

import anthropic

from agent.config import settings
from agent.usage_tracker import log_usage

logger = logging.getLogger("cratedigger-agent.extract")

MODEL = "claude-haiku-4-5-20251001"

# Entries per Anthropic call. 25 balances round-trip latency (one call ~500ms)
# against context overhead and JSONL parse complexity. Tune by measuring real
# crawls if needed.
BATCH_SIZE = 25

# Cap on concurrent in-flight calls. Anthropic's rate limits are generous on
# Haiku but we stay polite — 8 parallel is plenty for ~200 entries.
MAX_CONCURRENT = 8

# Maximum description chars sent to the LLM per entry. Article bodies can be
# long; the first ~400 chars almost always contain enough context to decide
# release-vs-news and pull an artist mention.
DESCRIPTION_CHARS = 400

_SYSTEM = """\
You extract music release info from blog and magazine entries.

For each entry, decide whether it announces a NEW MUSIC RELEASE (album, EP,
single, mixtape, compilation) and if so, pull the artist and the release title.

MOST ENTRIES ARE NOT RELEASES. Common non-release shapes:
- Festival lineup or programme announcements
- News stories about an artist (lawsuits, deaths, tour dates, beef)
- Podcast or interview series episodes
- Review columns about older music ("The Number Ones", "Listening Picks")
- Label news, signings, deals
- Listicles, year-end lists, opinion pieces

For non-releases, return is_release=false and leave artist and title null.

For releases, identify the primary artist (not the reviewer, not the editor).
If the entry mentions multiple artists in a "vs"/"featuring" form, return the
billed primary artist. If the title is itself the article headline ("Loud
Bloom"), use the description to find the artist.

OUTPUT
Return JSONL — one JSON object per input entry, in the same order as the
input, no surrounding text. Schema per line:

  {"id": <int>, "is_release": <bool>, "artist": <string|null>, "title": <string|null>}

EXAMPLES

Input:
  {"id": 1, "title": "Aldous Harding, \\"Train On The Island\\""}
Output:
  {"id": 1, "is_release": true, "artist": "Aldous Harding", "title": "Train On The Island"}

Input:
  {"id": 2, "title": "Spellbound Festival announces 2026 programme"}
Output:
  {"id": 2, "is_release": false, "artist": null, "title": null}

Input:
  {"id": 3, "title": "The Number Ones: BTS' \\"Butter\\""}
Output:
  {"id": 3, "is_release": false, "artist": null, "title": null}

Input:
  {"id": 4, "title": "Loud Bloom", "description": "Caroline Polachek's fifth album..."}
Output:
  {"id": 4, "is_release": true, "artist": "Caroline Polachek", "title": "Loud Bloom"}
"""


def _trim(s: str, n: int) -> str:
    s = (s or "").strip()
    return s[:n] + "…" if len(s) > n else s


def _build_user_prompt(entries: list[tuple[int, dict]]) -> str:
    payload = [
        {
            "id": i,
            "title": _trim(e.get("title") or "", 240),
            "description": _trim(e.get("description") or "", DESCRIPTION_CHARS),
        }
        for i, e in entries
    ]
    lines = "\n".join(json.dumps(p, ensure_ascii=False) for p in payload)
    return f"Entries:\n{lines}\n\nReturn JSONL output."


def _parse_jsonl(text: str) -> list[dict]:
    out: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "id" in obj:
            out.append(obj)
    return out


async def _extract_one_batch(
    client: anthropic.AsyncAnthropic,
    entries: list[tuple[int, dict]],
) -> dict[int, dict]:
    """Run extraction on one batch of up to BATCH_SIZE entries.

    Returns {id: result_dict}. Missing IDs (parse failure, refusal) are
    handled by the caller — they keep their original empty-artist state.
    """
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_user_prompt(entries)}],
        )
        log_usage("extract.batch", response, model=MODEL)
    except Exception as e:
        logger.error("extract batch failed: %s", e)
        return {}

    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip()

    parsed = _parse_jsonl(text)
    return {int(p["id"]): p for p in parsed if isinstance(p.get("id"), int)}


async def extract_releases(entries: list[dict]) -> list[dict]:
    """For each entry, classify release-vs-news and fill in artist + title.

    Input shape:  list of dicts with at least "title" and "description".
    Output shape: same list, in order, each entry annotated with:
      - is_release: bool
      - artist (overwritten if extraction returned one)
      - title (overwritten if extraction returned one)
    Entries the LLM couldn't classify keep is_release=False so the
    downstream dedup drops them (better to drop than fabricate).

    If ANTHROPIC_API_KEY is not configured, every entry is marked
    is_release=False and returned unchanged — no extraction happens.
    """
    if not entries:
        return []
    if not settings.anthropic_api_key:
        logger.warning(
            "extract_releases: ANTHROPIC_API_KEY not set — marking all "
            "%d empty-artist entries as non-release",
            len(entries),
        )
        return [{**e, "is_release": False} for e in entries]

    # Tag each entry with a stable batch-local id so we can map results back.
    indexed = list(enumerate(entries))
    batches = [indexed[i : i + BATCH_SIZE] for i in range(0, len(indexed), BATCH_SIZE)]
    logger.info(
        "extract_releases: %d entries → %d batches of up to %d",
        len(entries), len(batches), BATCH_SIZE,
    )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def run(batch: list[tuple[int, dict]]) -> dict[int, dict]:
        async with sem:
            return await _extract_one_batch(client, batch)

    batch_results = await asyncio.gather(*(run(b) for b in batches))

    # Merge — each batch returns {batch_local_id: result}. Map back to the
    # global index order.
    merged: dict[int, dict] = {}
    for r in batch_results:
        merged.update(r)

    out: list[dict] = []
    n_extracted = 0
    n_news = 0
    for i, entry in indexed:
        result = merged.get(i)
        if result is None:
            # Parse miss or refusal — treat as news (drop it).
            out.append({**entry, "is_release": False})
            continue
        is_release = bool(result.get("is_release"))
        artist = (result.get("artist") or "").strip()
        title = (result.get("title") or "").strip()
        if is_release and artist and title:
            n_extracted += 1
            out.append({
                **entry,
                "is_release": True,
                "artist": artist,
                "title": title,
            })
        else:
            n_news += 1
            out.append({**entry, "is_release": False})

    logger.info(
        "extract_releases: %d releases extracted, %d marked as news/non-release",
        n_extracted, n_news,
    )
    return out
