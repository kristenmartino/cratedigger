"""Tests for the RSS title-extraction heuristic.

Live samples from the first crawl runs drove these — each formatted feed
uses its own separator convention and the heuristic has to leave news
headlines untouched (empty artist) so the downstream pipeline can decide.
"""
from __future__ import annotations

from agent.sources.rss import parse_release_title


def test_em_dash_separator():
    assert parse_release_title("Burial — Untrue") == ("Burial", "Untrue")


def test_en_dash_separator():
    assert parse_release_title("Burial – Untrue") == ("Burial", "Untrue")


def test_hyphen_separator():
    assert parse_release_title("Burial - Untrue") == ("Burial", "Untrue")


def test_double_hyphen_reddit_listentothis():
    """r/listentothis enforces "Artist -- Title [Genre, Year]" — the
    double-hyphen pattern needs to win over the single hyphen."""
    assert parse_release_title("Burial -- Untrue [IDM, 2007]") == (
        "Burial",
        "Untrue [IDM, 2007]",
    )


def test_tilde_separator_acl():
    assert parse_release_title("SHHE ~ THALASSA") == ("SHHE", "THALASSA")


def test_double_colon_aquarium_drunkard():
    assert parse_release_title("Setting :: S/T") == ("Setting", "S/T")


def test_bandcamp_daily_smart_quoted_title():
    assert parse_release_title("Aldous Harding, “Train On The Island”") == (
        "Aldous Harding",
        "Train On The Island",
    )


def test_bandcamp_daily_straight_quoted_title():
    assert parse_release_title('Aldous Harding, "Train On The Island"') == (
        "Aldous Harding",
        "Train On The Island",
    )


def test_news_headline_with_incidental_comma_is_not_split():
    """The BD quoted-title pattern must NOT trigger on a bare comma headline
    or it would carve out a fake artist from feature articles."""
    headline = "The Best Contemporary Classical Music on Bandcamp, April 2026"
    assert parse_release_title(headline) == ("", headline)


def test_news_headline_with_no_separator_is_passed_through():
    headline = "Weird Nightmare’s Bandcamp Listening Picks"
    assert parse_release_title(headline) == ("", headline)


def test_empty_string():
    assert parse_release_title("") == ("", "")


def test_strips_surrounding_whitespace_in_parts():
    assert parse_release_title("  Burial   —   Untrue  ") == ("Burial", "Untrue")


def test_em_dash_wins_over_hyphen():
    """A title that contains both should prefer the em-dash split — it's the
    canonical "Artist — Title" form. The hyphen often appears inside titles
    ('Hard-Earned Optimism'), so it must be the lowest-priority separator."""
    assert parse_release_title("Hiss Golden Messenger — Hard-Earned Optimism") == (
        "Hiss Golden Messenger",
        "Hard-Earned Optimism",
    )
