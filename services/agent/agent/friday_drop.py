"""Friday surprise email delivery.

Companion to the Sunday digest. Every recommendation flagged as `withheld`
during the Sunday run has a `withhold_until` timestamp set to that
Saturday-night cron's `friday_drop_at()` (Friday 13:00 UTC, ~9 AM EDT).

This module:
  1. finds all withheld recommendations for one user where
     `withhold_until <= NOW()` and `withheld_delivered_at IS NULL`
  2. renders a Friday-shaped MJML email for each (smaller than Sunday)
  3. sends via Resend
  4. flips `withheld_delivered_at = NOW()` so we don't double-send

Idempotency lives in the column: a successful send marks the row;
a failure leaves it unmarked so the next Friday cron retries.

The Sunday cron route mirrors run-issue's fan-out — one user_id per
POST to /v1/friday-drop. This module is the per-user side.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    import resend
except ImportError:  # pragma: no cover — dev sandboxes without deps
    resend = None  # type: ignore[assignment]

from agent.config import settings
from agent.db import get_pool
from agent.email_template import build_friday_drop_mjml

logger = logging.getLogger("cratedigger-agent.friday_drop")


async def deliver_friday_drops_for_user(user_id: str) -> dict[str, Any]:
    """Render + send every pending Friday surprise for one user.

    Returns a summary dict:
        {
          "user_id": str,
          "eligible": int,         # how many pending pre-send
          "delivered": int,        # how many sent + marked
          "errors": list[str],     # per-row error messages
        }

    Never raises — partial failures are recorded in `errors` so the cron
    route can roll them up into a single ops alert.
    """
    summary: dict[str, Any] = {
        "user_id": user_id,
        "eligible": 0,
        "delivered": 0,
        "errors": [],
    }

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                r.id::text AS rec_id,
                r.source_attr,
                r.prose,
                r.cover_art_url AS rec_cover_art_url,
                rel.artist,
                rel.title AS release_title,
                rel.cover_art_url AS rel_cover_art_url,
                rel.bandcamp_url,
                rel.spotify_url,
                rel.apple_music_url,
                rel.youtube_url,
                rel.soundcloud_url,
                rel.url,
                i.issue_number,
                u.email AS to_email
              FROM recommendations r
              JOIN issues i ON i.id = r.issue_id
              JOIN users u ON u.id = i.user_id
              JOIN releases rel ON rel.id = r.release_id
             WHERE i.user_id = $1::uuid
               AND r.category = 'withheld'
               AND r.withhold_until IS NOT NULL
               AND r.withhold_until <= NOW()
               AND r.withheld_delivered_at IS NULL
             ORDER BY i.issue_number ASC
            """,
            user_id,
        )

    summary["eligible"] = len(rows)
    if not rows:
        logger.info("friday_drop: no eligible picks for user %s", user_id)
        return summary

    if not settings.resend_api_key:
        msg = "RESEND_API_KEY unset — cannot send"
        logger.warning("friday_drop: %s (user=%s, eligible=%d)", msg, user_id, len(rows))
        summary["errors"].append(msg)
        return summary
    if resend is None:  # pragma: no cover
        msg = "resend package not installed"
        summary["errors"].append(msg)
        return summary

    resend.api_key = settings.resend_api_key

    from agent.listen_links import build_listen_links

    for row in rows:
        rec_id = row["rec_id"]
        try:
            # All-platforms strip. Same shape Sunday emails use.
            listen_links = build_listen_links(dict(row))
            mjml_source = build_friday_drop_mjml({
                "issue_number": row["issue_number"],
                "artist": row["artist"],
                "release_title": row["release_title"],
                "source_attr": row["source_attr"],
                "prose": row["prose"],
                "cover_art_url": row["rec_cover_art_url"] or row["rel_cover_art_url"],
                "listen_links": listen_links,
            })
            # Defensive MJML dispatch — see agent/email_template.render_mjml
            # for why we don't pin to a specific function name.
            from agent.email_template import render_mjml
            html_body = await asyncio.to_thread(render_mjml, mjml_source)
            if not html_body:
                raise RuntimeError("MJML render returned empty html")

            subject = f"Crate Digger — Friday drop: {row['artist']}"
            send_payload = {
                "from": settings.resend_from_address,
                "to": [row["to_email"]],
                "subject": subject,
                "html": html_body,
            }
            await asyncio.to_thread(resend.Emails.send, send_payload)

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE recommendations
                       SET withheld_delivered_at = NOW()
                     WHERE id = $1::uuid
                    """,
                    rec_id,
                )
            summary["delivered"] += 1
            logger.info(
                "friday_drop: delivered rec %s (%s — %s) to %s",
                rec_id, row["artist"], row["release_title"], row["to_email"],
            )
        except Exception as e:
            err = f"rec {rec_id}: {e}"
            logger.error("friday_drop: %s", err)
            summary["errors"].append(err)

    return summary
