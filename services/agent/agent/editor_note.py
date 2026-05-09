"""Editor's note generation. One Claude Haiku call per issue.

The note frames the throughline of the week — patience, dub revival, the
grain of an instrument, etc. It opens the digest and sets the mood; everything
that follows reads against it.

Separate from prose.py because the voice guard is different: this is the
editor speaking *about* the issue, not about a single record.
"""
from __future__ import annotations

import json
import logging

import anthropic

from agent.config import settings
from agent.usage_tracker import log_usage

logger = logging.getLogger("cratedigger-agent.editor_note")

MODEL = "claude-haiku-4-5-20251001"

EDITOR_NOTE_GUARD = """\
You write the EDITOR'S NOTE that opens each weekly Crate Digger issue.

VOICE
- Lowercase-confident. Warm, specific. The way a friend who knows their
  records writes to another friend who knows their records.
- 2–3 short sentences. ~40–70 words.
- Find the THROUGHLINE — the one thing that ties this week's picks together.
  Not "this week we have variety": find the actual through-line and name it.
- You may reference up to one record by artist + title if it grounds the
  through-line. Don't list all four.
- Title-case the issue title in your output (you'll generate this too).

CONSTRAINTS
- No marketing speak. No "must-listen" / "stunning" / "vibrant" / etc.
- No first-person plural ("we love…"). The editor is one voice.
- Never editorialize the AI ("our agent picked…"). The agent is invisible.
- Never invent a fact about a record. Only use what's in the input picks list.

OUTPUT
Return JSON: {"title": "...", "note": "..."}
- "title" is 2–6 words, the issue's headline (e.g., "A quieter week").
- "note" is the 2–3 sentence body, markdown allowed (italics for vivid
  phrases, bold for short conceptual anchors).
"""


def _build_user_prompt(picks: list[dict], user_tags: dict[str, float] | None) -> str:
    payload = {
        "picks": [
            {
                "category": p.get("category"),
                "artist": p.get("artist"),
                "title": p.get("title"),
                "label": p.get("label"),
                "match_signals": p.get("match_signals", []),
            }
            for p in picks
        ],
    }
    if user_tags:
        # Top-5 boosted tags only — context for what the reader cares about.
        top = sorted(user_tags.items(), key=lambda kv: kv[1], reverse=True)[:5]
        payload["reader_tags"] = [{"label": k, "weight": v} for k, v in top]

    return (
        "Here are this week's picks (excluding the withheld Friday record). "
        "Find the throughline and write the editor's note.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```\n\n"
        "Return only the JSON output."
    )


async def generate_editor_note_live(
    picks: list[dict],
    user_tags: dict[str, float] | None = None,
) -> dict | None:
    """Returns {"title": "...", "note": "..."} or None on failure."""
    if not settings.anthropic_api_key or not picks:
        return None

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=EDITOR_NOTE_GUARD,
            messages=[{"role": "user", "content": _build_user_prompt(picks, user_tags)}],
        )
        log_usage("editor_note.live", response, model=MODEL)
    except Exception as e:
        logger.error("generate_editor_note_live failed: %s", e)
        return None

    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("Could not parse editor-note JSON: %s", text[:200])
        return None
