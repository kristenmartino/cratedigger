"""End-to-end render snapshot — template → MJML → HTML.

Most of the email bugs we shipped this week could have been caught by a
test in this shape:

  PR #16  cannot import 'mjml_to_html' from 'mjml'   → render_mjml raises
  PR #17  email_html not propagating through state   → out-of-scope here
                                                       (graph-state bug)
  PR #18  markdown markers literal in email
          cover image missing
          listen link missing                        → assertions below

This test invokes `build_issue_mjml` + `render_mjml` against a known
fixture and asserts on the *rendered HTML* — not just the MJML source.
That's the contract that matters: what actually lands in the reader's
inbox.

Skipped when `mjml-python` isn't installed (sandbox dev environments).
CI installs everything from requirements.txt; this is the production
contract test.
"""
from __future__ import annotations

import pytest

from agent.email_template import build_issue_mjml, render_mjml


# Detect whether mjml-python is importable in the current env. We don't
# call render_mjml at collection time — its import is internal — but if
# the package is missing the test would skip anyway, so do it here.
try:
    import mjml  # noqa: F401
    MJML_AVAILABLE = True
except ImportError:
    MJML_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not MJML_AVAILABLE,
    reason="mjml-python not installed in this environment (CI has it)",
)


@pytest.fixture
def fixture_issue() -> dict:
    """A fully-populated issue with the patterns Haiku's prose produces:
    inline *italic* / **bold** markdown, cover URL, Bandcamp listen URL.
    Mirrors the shape render_email_node builds from the joined DB query."""
    return {
        "issue_number": 42,
        "publish_date": "2026-05-17",
        "title": "Patience as Practice",
        "editor_note": (
            "This week leans on *grain* and **stillness** — records that "
            "treat silence as an instrument."
        ),
        "recommendations": [
            {
                "category": "lead",
                "source_attr": "boomkat",
                "prose": (
                    "Burial's *fourth* record refuses easy categories. The "
                    "low end is **palpable**, the vocal collage haunted."
                ),
                "position": 1,
                "artist": "Burial",
                "release_title": "Untrue",
                "cover_art_url": "https://example.com/cover-untrue.jpg",
                "listen_url": "https://burial.bandcamp.com/album/untrue",
            },
            {
                "category": "steady",
                "source_attr": "aquarium-drunkard",
                "prose": "A patient, gathering record.",
                "position": 2,
                "artist": "Setting",
                "release_title": "S/T",
                "cover_art_url": None,
                "listen_url": "https://setting.bandcamp.com",
            },
            {
                "category": "withheld",
                "source_attr": "hardwax",
                "prose": "Friday surprise — not in Sunday's email.",
                "position": 5,
                "artist": "Boards of Canada",
                "release_title": "Tape 05",
                "cover_art_url": "https://example.com/cover-boc.jpg",
                "listen_url": "https://boc.bandcamp.com",
            },
        ],
    }


# ── Smoke: the pipeline actually produces non-empty HTML ───────────────────


def test_render_pipeline_produces_html(fixture_issue):
    """Catches PR #16-class bugs: if mjml's API has drifted again,
    render_mjml raises with a diagnostic naming actual exports — caught
    here as a test failure before it reaches production."""
    mjml_source = build_issue_mjml(fixture_issue)
    html = render_mjml(mjml_source)
    assert html.startswith("<!doctype") or html.startswith("<html")
    assert "</html>" in html
    assert len(html) > 1000, "html unexpectedly small — render likely failed"


# ── Markdown emphasis renders, not literal ────────────────────────────────


def test_editor_note_italic_renders_as_em(fixture_issue):
    """Caught PR #18 class: `*grain*` was rendering literally in
    production. The template now converts to <em>; this asserts the
    converted form ends up in the final HTML (not the MJML source)."""
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "<em>grain</em>" in html
    assert "*grain*" not in html


def test_editor_note_bold_renders_as_strong(fixture_issue):
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "<strong>stillness</strong>" in html
    assert "**stillness**" not in html


def test_prose_italic_and_bold_render(fixture_issue):
    """Prose has the same markdown treatment as editor's note. Both
    paths use _md_inline; this catches drift if one diverges."""
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "<em>fourth</em>" in html
    assert "<strong>palpable</strong>" in html


# ── Cover art image rendered when URL present, omitted when null ──────────


def test_cover_image_renders_when_url_present(fixture_issue):
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "https://example.com/cover-untrue.jpg" in html
    assert "<img" in html


def test_cover_image_omitted_when_url_null(fixture_issue):
    """The Steady pick (Setting) has cover_art_url=None. The HTML must
    not reference it (no broken-image render in the inbox)."""
    html = render_mjml(build_issue_mjml(fixture_issue))
    # Setting block is present...
    assert "Setting" in html and "S/T" in html
    # ...but no img tag with a falsy/literal-None src
    assert 'src=""' not in html
    assert "src=\"None\"" not in html


# ── Listen link rendered as a clickable anchor with href ──────────────────


def test_listen_link_renders_with_href(fixture_issue):
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "https://burial.bandcamp.com/album/untrue" in html
    # MJML's mj-button compiles to an <a> tag with href — the URL has to
    # appear inside an href attribute, not just as plain text in the body.
    assert 'href="https://burial.bandcamp.com/album/untrue"' in html
    assert "Listen ↗" in html


# ── Withheld pick is excluded from Sunday email ───────────────────────────


def test_withheld_pick_not_in_sunday_html(fixture_issue):
    """Withheld picks ship in the Friday surprise email. Sunday must not
    surface them. The cover_art_url for Boards of Canada is present in
    the fixture deliberately — if filter logic broke, the URL would
    appear in HTML."""
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "Boards of Canada" not in html
    assert "Tape 05" not in html
    assert "cover-boc.jpg" not in html
    assert "boc.bandcamp.com" not in html


# ── Lead + Steady records both present ───────────────────────────────────


def test_lead_and_steady_records_rendered(fixture_issue):
    html = render_mjml(build_issue_mjml(fixture_issue))
    # Lead
    assert "Burial" in html
    assert "Untrue" in html
    assert "boomkat" in html
    # Steady
    assert "Setting" in html
    assert "S/T" in html
    assert "aquarium-drunkard" in html


# ── Editor's note throughline body present ────────────────────────────────


def test_editor_note_body_appears(fixture_issue):
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "This week leans on" in html
    assert "records that treat silence" in html


# ── View-in-browser anchor when app_base_url provided ─────────────────────


def test_view_in_browser_anchor_uses_real_url(fixture_issue):
    html = render_mjml(
        build_issue_mjml(fixture_issue, app_base_url="https://cratedigger.example")
    )
    assert 'href="https://cratedigger.example/issue/42"' in html
    assert "Read in browser" in html


def test_view_in_browser_anchor_omitted_when_not_passed(fixture_issue):
    """Default builder doesn't include the anchor (it's opt-in)."""
    html = render_mjml(build_issue_mjml(fixture_issue))
    assert "Read in browser" not in html
