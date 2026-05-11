"""Failure alerts via Resend.

Best-effort plain-text email alerts when something in the pipeline goes
sideways: an agent_run lands in 'failed' state, the cron route 5xx's,
a per-user trigger throws inside the fan-out, etc.

Gating: both RESEND_API_KEY and OPS_ALERT_EMAIL must be set. If either
is missing, alerts skip silently with an info-level log. This is
deliberate — the failure path must never raise on top of the original
failure (alerting that alerted-failed-during-failure gets nobody anywhere).

The Next.js cron route has a mirror at apps/web/src/lib/alerts.ts that
uses plain fetch to Resend's REST API (no npm dep). Same OPS_ALERT_EMAIL
recipient, same FROM, same subject convention so alerts thread together
in the inbox regardless of which side raised.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    import resend
except ImportError:  # pragma: no cover — only hit in dev sandboxes without deps
    resend = None  # type: ignore[assignment]

from agent.config import settings

logger = logging.getLogger("cratedigger-agent.alerts")


async def send_failure_alert(subject: str, body: str) -> None:
    """Best-effort failure alert. Never raises.

    Returns early when RESEND_API_KEY or OPS_ALERT_EMAIL is unset (CI,
    local dev). Caller doesn't need to handle errors from this — any
    Resend exception is logged and swallowed.
    """
    if not (settings.resend_api_key and settings.ops_alert_email):
        logger.info(
            "alert: skipped (RESEND_API_KEY=%s OPS_ALERT_EMAIL=%s)",
            bool(settings.resend_api_key), bool(settings.ops_alert_email),
        )
        return
    if resend is None:  # pragma: no cover
        logger.error("alert: resend package not installed — cannot send")
        return

    resend.api_key = settings.resend_api_key
    payload: dict[str, Any] = {
        "from": settings.resend_from_address,
        "to": [settings.ops_alert_email],
        "subject": subject,
        "text": body,
    }
    try:
        await asyncio.to_thread(resend.Emails.send, payload)
        logger.info("alert: sent %r to %s", subject, settings.ops_alert_email)
    except Exception as e:
        # Don't let an alert failure mask the original failure.
        logger.error("alert: send failed (%s)", e)
