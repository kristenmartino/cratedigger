"""Tests for the deterministic parts of agent/extract.py.

The LLM call itself is exercised in production; here we just cover the
batching shape, JSONL parsing (including model quirks), and the no-API-key
path. We don't make real Anthropic calls.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from agent.extract import (
    BATCH_SIZE,
    _build_user_prompt,
    _parse_jsonl,
    _trim,
    extract_releases,
)


# ── _trim ───────────────────────────────────────────────────────────────


def test_trim_short_string_unchanged():
    assert _trim("hello", 50) == "hello"


def test_trim_long_string_gets_ellipsis():
    assert _trim("a" * 100, 10) == "a" * 10 + "…"


def test_trim_strips_surrounding_whitespace():
    assert _trim("   hello   ", 50) == "hello"


def test_trim_none_input():
    assert _trim(None, 10) == ""  # type: ignore[arg-type]


# ── _build_user_prompt ──────────────────────────────────────────────────


def test_user_prompt_contains_each_entry_as_jsonline():
    entries = [
        (0, {"title": "A", "description": "one"}),
        (1, {"title": "B", "description": "two"}),
    ]
    prompt = _build_user_prompt(entries)
    assert '"id": 0' in prompt
    assert '"id": 1' in prompt
    assert '"title": "A"' in prompt
    assert '"title": "B"' in prompt
    assert "Return JSONL output." in prompt


def test_user_prompt_preserves_unicode():
    entries = [(0, {"title": "SHHE — THALASSA", "description": "Tórshavn"})]
    prompt = _build_user_prompt(entries)
    assert "SHHE — THALASSA" in prompt
    assert "Tórshavn" in prompt


# ── _parse_jsonl ────────────────────────────────────────────────────────


def test_parse_jsonl_clean_lines():
    text = (
        '{"id": 0, "is_release": true, "artist": "Burial", "title": "Untrue"}\n'
        '{"id": 1, "is_release": false, "artist": null, "title": null}\n'
    )
    result = _parse_jsonl(text)
    assert len(result) == 2
    assert result[0]["artist"] == "Burial"
    assert result[1]["is_release"] is False


def test_parse_jsonl_skips_blank_lines():
    text = (
        '\n'
        '{"id": 0, "is_release": true, "artist": "A", "title": "T"}\n'
        '\n'
        '   \n'
    )
    assert len(_parse_jsonl(text)) == 1


def test_parse_jsonl_skips_markdown_fences():
    """Some Haiku responses get wrapped in ```jsonl ... ``` fences; strip them
    rather than die on parse errors."""
    text = (
        '```jsonl\n'
        '{"id": 0, "is_release": true, "artist": "A", "title": "T"}\n'
        '```\n'
    )
    result = _parse_jsonl(text)
    assert len(result) == 1
    assert result[0]["artist"] == "A"


def test_parse_jsonl_skips_unparseable_lines():
    text = (
        '{"id": 0, "is_release": true, "artist": "A", "title": "T"}\n'
        'this is not json\n'
        '{"id": 1, "is_release": false}\n'
    )
    result = _parse_jsonl(text)
    assert len(result) == 2
    assert result[0]["id"] == 0
    assert result[1]["id"] == 1


def test_parse_jsonl_skips_objects_without_id():
    text = (
        '{"id": 0, "is_release": true, "artist": "A", "title": "T"}\n'
        '{"is_release": true, "artist": "B"}\n'
    )
    result = _parse_jsonl(text)
    assert len(result) == 1


# ── extract_releases (no API key) ───────────────────────────────────────


def test_extract_releases_no_api_key_marks_all_as_news():
    """Without ANTHROPIC_API_KEY we must NOT make network calls, and every
    entry must come back is_release=False so the downstream pipeline drops
    them instead of fabricating data."""
    entries = [
        {"title": "Some Article", "description": "blah"},
        {"title": "Other Thing", "description": "blah"},
    ]
    with patch("agent.extract.settings") as mock_settings:
        mock_settings.anthropic_api_key = ""
        out = asyncio.run(extract_releases(entries))
    assert len(out) == 2
    assert all(e["is_release"] is False for e in out)
    # Original fields are preserved untouched
    assert out[0]["title"] == "Some Article"


def test_extract_releases_empty_input():
    out = asyncio.run(extract_releases([]))
    assert out == []


# ── batching sanity ─────────────────────────────────────────────────────


def test_batch_size_is_reasonable():
    """Sanity check: BATCH_SIZE must be small enough that a batch fits in
    Haiku's context window (~200k input tokens) with room for the JSONL
    response. 25 entries × ~600 tokens of overhead = ~15k tokens; comfortable."""
    assert 10 <= BATCH_SIZE <= 50
