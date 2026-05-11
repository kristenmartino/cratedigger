"""Tests for the failure-alert helper.

The alert path must never raise — if it dies on top of an already-failed
pipeline, nobody hears about either failure. These tests validate the
silent-skip cases and the swallowed-exception case.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from agent.alerts import send_failure_alert


def test_skips_silently_when_api_key_unset():
    """No RESEND_API_KEY → no send, no raise."""
    with patch("agent.alerts.settings") as mock_settings:
        mock_settings.resend_api_key = ""
        mock_settings.ops_alert_email = "ops@example.com"
        mock_settings.resend_from_address = "editor@example.com"
        # If this raises, the test fails.
        asyncio.run(send_failure_alert("subj", "body"))


def test_skips_silently_when_ops_email_unset():
    """No OPS_ALERT_EMAIL → no send, no raise."""
    with patch("agent.alerts.settings") as mock_settings:
        mock_settings.resend_api_key = "key"
        mock_settings.ops_alert_email = ""
        mock_settings.resend_from_address = "editor@example.com"
        asyncio.run(send_failure_alert("subj", "body"))


def test_swallows_resend_exceptions():
    """A Resend error must not propagate — the original failure is more
    important than the alert."""
    with patch("agent.alerts.settings") as mock_settings, \
         patch("agent.alerts.resend") as mock_resend:
        mock_settings.resend_api_key = "key"
        mock_settings.ops_alert_email = "ops@example.com"
        mock_settings.resend_from_address = "editor@example.com"
        mock_resend.Emails.send.side_effect = RuntimeError("Resend down")
        # If this raises, the test fails.
        asyncio.run(send_failure_alert("subj", "body"))


def test_sends_with_expected_payload_shape():
    """Happy path: when both env vars are set, the helper hands Resend a
    well-formed payload."""
    captured: dict = {}

    def fake_send(payload):
        captured.update(payload)

    with patch("agent.alerts.settings") as mock_settings, \
         patch("agent.alerts.resend") as mock_resend:
        mock_settings.resend_api_key = "key"
        mock_settings.ops_alert_email = "ops@example.com"
        mock_settings.resend_from_address = "editor@example.com"
        mock_resend.Emails.send.side_effect = fake_send
        asyncio.run(send_failure_alert("[CD] test", "body of alert"))

    assert captured["from"] == "editor@example.com"
    assert captured["to"] == ["ops@example.com"]
    assert captured["subject"] == "[CD] test"
    assert captured["text"] == "body of alert"
