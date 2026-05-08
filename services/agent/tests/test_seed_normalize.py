"""Tests for the (artist, title) normalization used by dedup.

The normalize() function lives in scripts/seed.py (and the same shape is
duplicated in agent/sources/rss.py). Tests live here because the seed
script is where the canonical version is referenced first.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from seed import normalize  # noqa: E402


def test_lowercase():
    assert normalize("LORAINE JAMES") == "loraine james"


def test_punctuation_stripped():
    assert normalize("upsammy") == "upsammy"
    assert normalize("Klein!") == "klein"
    assert normalize("Yu Su — Melaleuca") == "yu su melaleuca"


def test_unicode_folded_to_ascii():
    assert normalize("Caterina Barbieri") == "caterina barbieri"
    assert normalize("Nous'klaer") == "nousklaer"
    # Smart quotes get stripped (since the apostrophe isn't ASCII)
    assert normalize("don’t look") == "dont look"


def test_collapses_whitespace():
    assert normalize("  Loraine    James  ") == "loraine james"


def test_empty_input():
    assert normalize("") == ""
    assert normalize("   ") == ""
