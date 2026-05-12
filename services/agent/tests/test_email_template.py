"""Tests for the Sunday issue MJML template.

Most of the value here is "does it produce valid MJML that mjml_to_html
accepts without errors"; visual fidelity is tested by eyeballing the
preview during development.
"""
from __future__ import annotations

import pytest

from agent.email_template import build_friday_drop_mjml, build_issue_mjml


@pytest.fixture
def sample_issue() -> dict:
    return {
        "issue_number": 7,
        "publish_date": "2026-05-17",
        "title": "A quieter week",
        "editor_note": (
            "This week is about *grain* — the texture of breath against a "
            "microphone, the hum a room remembers."
        ),
        "recommendations": [
            {
                "category": "lead",
                "source_attr": "boomkat",
                "prose": "A standout from the experimental edges.",
                "position": 1,
                "artist": "SHHE",
                "release_title": "THALASSA",
            },
            {
                "category": "steady",
                "source_attr": "aquarium-drunkard",
                "prose": "Patient, gathering Americana.",
                "position": 2,
                "artist": "Setting",
                "release_title": "S/T",
            },
            {
                "category": "steady",
                "source_attr": "bandcamp-daily",
                "prose": "A clean-edged second record.",
                "position": 3,
                "artist": "Aldous Harding",
                "release_title": "Train On The Island",
            },
            {
                "category": "stretch",
                "source_attr": "hardwax",
                "prose": "A late-night techno reach.",
                "position": 4,
                "artist": "Mary Yuzovskaya",
                "release_title": "Bin008",
            },
            {
                "category": "withheld",
                "source_attr": "resident-advisor",
                "prose": "Held for Friday.",
                "position": 5,
                "artist": "Boards of Canada",
                "release_title": "Tape 05",
            },
        ],
    }


def test_builds_valid_mjml_shape(sample_issue):
    mjml = build_issue_mjml(sample_issue)
    assert mjml.startswith("<mjml>")
    assert mjml.strip().endswith("</mjml>")
    assert "<mj-head>" in mjml
    assert "<mj-body" in mjml


def test_issue_number_and_padding(sample_issue):
    mjml = build_issue_mjml(sample_issue)
    # Eyebrow uses raw issue_number; display uses zero-padded
    assert "Issue 7 ·" in mjml
    assert ">07<" in mjml


def test_withheld_pick_is_excluded(sample_issue):
    """Sunday email never shows the withheld pick — that's Friday's job."""
    mjml = build_issue_mjml(sample_issue)
    assert "Boards of Canada" not in mjml
    assert "Tape 05" not in mjml
    # The other four still render
    assert "SHHE" in mjml
    assert "THALASSA" in mjml
    assert "Setting" in mjml
    assert "Mary Yuzovskaya" in mjml


def test_escapes_html_in_user_supplied_strings():
    """An editor's note containing < or & must not break the MJML parser."""
    issue = {
        "issue_number": 1,
        "publish_date": "2026-05-17",
        "title": "Title with <em>tag</em> & ampersand",
        "editor_note": "Body with <script>alert(1)</script> & co",
        "recommendations": [],
    }
    mjml = build_issue_mjml(issue)
    assert "<script>" not in mjml  # escaped, not literal
    assert "&lt;script&gt;" in mjml
    assert "&amp;" in mjml


def test_handles_zero_recommendations():
    """An issue with no picks (e.g. empty week) should still produce valid MJML."""
    issue = {
        "issue_number": 1,
        "publish_date": "2026-05-17",
        "title": "Empty",
        "editor_note": "",
        "recommendations": [],
    }
    mjml = build_issue_mjml(issue)
    assert mjml.startswith("<mjml>")
    assert mjml.strip().endswith("</mjml>")


def test_recommendation_block_includes_required_fields(sample_issue):
    mjml = build_issue_mjml(sample_issue)
    # Lead block content
    assert "lead · via boomkat" in mjml
    assert "SHHE" in mjml
    assert "THALASSA" in mjml
    assert "A standout from the experimental edges." in mjml


# ── Friday drop template ────────────────────────────────────────────────


@pytest.fixture
def sample_friday_drop() -> dict:
    return {
        "issue_number": 7,
        "artist": "Boards of Canada",
        "release_title": "Tape 05",
        "source_attr": "resident-advisor",
        "prose": "The withheld pick, finally surfacing.",
    }


def test_friday_drop_builds_valid_mjml(sample_friday_drop):
    mjml = build_friday_drop_mjml(sample_friday_drop)
    assert mjml.startswith("<mjml>")
    assert mjml.strip().endswith("</mjml>")
    assert "<mj-head>" in mjml
    assert "<mj-body" in mjml


def test_friday_drop_subject_references_artist(sample_friday_drop):
    """The mj-title (rendered as email subject metadata by some clients)
    should include the artist so a forwarded preview is meaningful."""
    mjml = build_friday_drop_mjml(sample_friday_drop)
    assert "Friday drop: Boards of Canada" in mjml


def test_friday_drop_includes_issue_number_and_record(sample_friday_drop):
    mjml = build_friday_drop_mjml(sample_friday_drop)
    assert "From Issue 7" in mjml
    assert "Boards of Canada" in mjml
    assert "Tape 05" in mjml
    assert "resident-advisor" in mjml
    assert "The withheld pick, finally surfacing." in mjml


# ── render_mjml dispatch ────────────────────────────────────────────────


def _stub_mjml(**exports):
    """Build a fake mjml module with the given exports."""
    import sys
    import types
    mod = types.ModuleType("mjml")
    for name, value in exports.items():
        setattr(mod, name, value)
    sys.modules["mjml"] = mod
    return mod


def test_render_mjml_dispatches_to_mjml_to_html():
    """The original API name we guessed — kept for newer mjml-python versions."""
    from agent.email_template import render_mjml

    _stub_mjml(mjml_to_html=lambda s: type("R", (), {"html": f"<html>{s}</html>"})())
    out = render_mjml("<mjml>x</mjml>")
    assert out == "<html><mjml>x</mjml></html>"


def test_render_mjml_falls_back_to_mjml2html():
    """v1.4.0 production error: the only working function may be mjml2html."""
    from agent.email_template import render_mjml

    _stub_mjml(mjml2html=lambda s: {"html": f"<from-mjml2html>{s}</from-mjml2html>"})
    out = render_mjml("<mjml>y</mjml>")
    assert out == "<from-mjml2html><mjml>y</mjml></from-mjml2html>"


def test_render_mjml_accepts_raw_string_result():
    """Some MJML libs return the HTML string directly, not wrapped."""
    from agent.email_template import render_mjml

    _stub_mjml(render=lambda s: f"<raw>{s}</raw>")
    out = render_mjml("<mjml>z</mjml>")
    assert out == "<raw><mjml>z</mjml></raw>"


def test_render_mjml_diagnostic_on_total_miss():
    """When no known API name resolves, the error must include the actual
    `dir(mjml)` listing so the next fix has a definitive answer."""
    from agent.email_template import render_mjml

    _stub_mjml(some_other_function=lambda s: s, another_one=42)

    try:
        render_mjml("<mjml>x</mjml>")
    except RuntimeError as e:
        msg = str(e)
        assert "some_other_function" in msg
        assert "another_one" in msg
        assert "No known MJML render function" in msg
    else:
        raise AssertionError("expected RuntimeError on total miss")


def test_friday_drop_escapes_html_in_fields():
    drop = {
        "issue_number": 1,
        "artist": "<script>alert(1)</script>",
        "release_title": "Title & co",
        "source_attr": "src",
        "prose": "prose with <em>tags</em>",
    }
    mjml = build_friday_drop_mjml(drop)
    assert "<script>alert(1)</script>" not in mjml
    assert "&lt;script&gt;" in mjml
    assert "&amp;" in mjml
