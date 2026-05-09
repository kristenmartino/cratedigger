"""Editorial prose generation via Claude Haiku 4.5.

Sift's primer_generator.py was the strongest pattern match; the voice-guard
header structure is adapted here for music-domain editorial writing.

Key invariant (per CLAUDE.md): the LLM must NEVER hallucinate catalog numbers,
release dates, label affiliations. All structured facts come from the release
record passed in as context. The LLM produces voice; the data layer produces
truth.

Two paths:
  - Live (sync): `generate_prose_live(...)` for one-off generation (e.g. dev,
    or the pull-quote post-process)
  - Batch (async via Message Batches): `submit_prose_batch(...)` for the full
    weekly issue. Results land via the batch_poller's `prose` handler.
"""
from __future__ import annotations

import json
import logging

import anthropic

from agent.batch_client import submit_batch
from agent.config import settings
from agent.usage_tracker import log_usage

logger = logging.getLogger("cratedigger-agent.prose")

MODEL = "claude-haiku-4-5-20251001"
BATCH_KIND = "prose"

VOICE_GUARD = """\
You write the editorial paragraphs for Crate Digger, a weekly music recommendations digest.

VOICE
- Warm, specific, lowercase-confident. Never marketing-speak.
- Speak to the reader as someone who already trusts the publication.
- Reference the user's prior taste signals when relevant ("you flagged X back in February").
- Use italics (*…*) for vivid phrases worth re-reading.
- Use **bold** for short conceptual anchors (a label name, a sound descriptor) — not for emphasis.

LENGTH
- 2–3 short paragraphs. Tight. ~120–160 words total.
- One pull-quote-worthy phrase per paragraph if possible.

CONSTRAINTS
- NEVER invent a catalog number, release date, label name, or artist bio.
  Every factual claim must come from the structured data in the prompt.
  If you're not sure, omit the fact.
- NEVER use the words "vibrant," "stunning," "unique," "groundbreaking,"
  "must-listen," or any review-blurb cliché. Music criticism, not marketing.
- NEVER editorialize the agent ("the AI thinks...", "our system suggests...").
  The newsletter speaks as a human editor; the agent is invisible.

INPUT SHAPE (you'll receive)
- title, artist, label, catalog_number, release_date
- category: "lead" | "steady" | "stretch" | "withheld"
- match_signals: list of {label, weight} pairs (these are the model's actual
  reasons for surfacing the record — incorporate them naturally if they fit)
- editor's note from the issue (sets the throughline)

OUTPUT
Return JSON: {"prose": "...", "pull_quote": "..." (only for lead)}
The prose is markdown. The pull-quote is a 4–8 word phrase pulled verbatim
from the prose (only for category="lead").
"""


def _build_user_prompt(context: dict) -> str:
    return (
        "Generate editorial prose for this record:\n\n"
        f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
        "Return only the JSON output."
    )


async def generate_prose_live(context: dict) -> dict | None:
    """Synchronous generation. Used for one-off prose during dev/testing.
    Production runs go through `submit_prose_batch` for the 50% discount.
    """
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY not set; returning empty prose")
        return None

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=VOICE_GUARD,
            messages=[{"role": "user", "content": _build_user_prompt(context)}],
        )
        log_usage("prose.live", response, model=MODEL)
    except Exception as e:
        logger.error("generate_prose_live failed: %s", e)
        return None

    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Recovery: try to extract a JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("Could not parse prose JSON: %s", text[:200])
        return None


PULL_QUOTE_GUARD = """\
You are picking ONE pull quote for a Crate Digger editorial paragraph.

RULES
- 4 to 8 words.
- Verbatim from the prose (no rewording, no punctuation changes besides
  trimming a trailing comma/period). Preserve italics/bold markdown if part
  of the chosen phrase.
- Pick the line that would still mean something on its own — concrete imagery
  or a specific claim, not a connective fragment.
- Output only the phrase. No quotes around it. No explanation.
"""


async def generate_pull_quote_live(prose: str) -> str | None:
    """Single-shot Haiku call to extract a pull quote from a piece of prose."""
    if not settings.anthropic_api_key or not prose:
        return None

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=40,
            system=PULL_QUOTE_GUARD,
            messages=[{"role": "user", "content": prose}],
        )
        log_usage("pull_quote.live", response, model=MODEL)
    except Exception as e:
        logger.error("generate_pull_quote_live failed: %s", e)
        return None

    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip().strip('"').strip("'")

    return text or None


async def submit_prose_batch(contexts: list[dict]) -> str | None:
    """Submit a batch of prose generation requests. Each context is one record."""
    requests = []
    for i, ctx in enumerate(contexts):
        custom_id = f"prose-{ctx.get('release_id', i)}"
        requests.append({
            "custom_id": custom_id,
            "params": {
                "model": MODEL,
                "max_tokens": 600,
                "system": VOICE_GUARD,
                "messages": [{"role": "user", "content": _build_user_prompt(ctx)}],
            },
        })
    return await submit_batch(BATCH_KIND, requests, metadata={"count": len(requests)})
