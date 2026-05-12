"""Python MJML template for the Sunday issue email.

Ported from apps/email/src/templates/issue.mjml.ts so the agent can render
emails without shelling out to Node. Visual structure stays in sync with
the TS template (same wordmark, same per-record block shape, same closing
sign-off) — if you tweak one, mirror the other or both will drift.

The renderer is mjml-python (in requirements.txt at v1.4.0). API shape
varies across versions — see render_mjml() below for the defensive
dispatch.

This template intentionally drops the withheld pick — that one ships in
Friday's surprise email, not Sunday's.
"""
from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger("cratedigger-agent.email_template")


# ── MJML renderer wrapper ────────────────────────────────────────────────
#
# mjml-python's API has drifted across versions. Production logs at
# v1.4.0 showed `cannot import name 'mjml_to_html' from 'mjml'`. Rather
# than pin to a specific version and risk this again on the next bump,
# we dispatch at runtime: try every known function-name shape, fall back
# to a class-based call if any of those packages is what's installed.
#
# On total miss, we raise with the actual `dir(mjml)` output so the next
# fix has a definitive answer instead of more guessing.

_KNOWN_FN_NAMES = ("mjml_to_html", "mjml2html", "render", "to_html")


def render_mjml(source: str) -> str:
    """Render MJML source to HTML. Tolerates mjml-python API drift.

    Returns the HTML body string. Raises RuntimeError on miss with a
    diagnostic listing of what IS exported by the `mjml` package.
    """
    import mjml  # local import — module load shouldn't depend on the package

    for fn_name in _KNOWN_FN_NAMES:
        fn = getattr(mjml, fn_name, None)
        if not callable(fn):
            continue
        try:
            result = fn(source)
        except Exception as e:
            logger.warning("render_mjml: %s(...) raised %s — trying next API", fn_name, e)
            continue
        # Result shape also varies: namedtuple-like with .html, dict
        # with 'html', or raw string.
        html_body = (
            getattr(result, "html", None)
            or (result.get("html") if isinstance(result, dict) else None)
            or (result if isinstance(result, str) else None)
        )
        if html_body:
            logger.info("render_mjml: dispatched via mjml.%s", fn_name)
            return html_body

    # Class-based API (older mjml-python shapes)
    klass = getattr(mjml, "MJML", None)
    if klass is not None:
        try:
            inst = klass(source)
            for method_name in ("to_html", "render", "html"):
                method = getattr(inst, method_name, None)
                if callable(method):
                    out = method()
                    if isinstance(out, str):
                        logger.info("render_mjml: dispatched via mjml.MJML.%s", method_name)
                        return out
        except Exception as e:
            logger.warning("render_mjml: mjml.MJML(...) raised %s", e)

    # Nothing worked — surface the actual exported symbols so the next
    # patch can target them exactly.
    available = sorted(s for s in dir(mjml) if not s.startswith("_"))
    raise RuntimeError(
        f"No known MJML render function in mjml package. "
        f"Tried: {list(_KNOWN_FN_NAMES) + ['MJML(...).to_html', '.render', '.html']}. "
        f"Available exports: {available}"
    )




def build_issue_mjml(issue: dict[str, Any]) -> str:
    """Build the MJML source string for a Sunday issue.

    Expected shape (matches the DB join in render_email_node):

        {
          "issue_number": int,
          "publish_date": str,           # ISO date
          "title": str,
          "editor_note": str,
          "recommendations": [
            {
              "category": "lead" | "steady" | "stretch" | "withheld",
              "source_attr": str,
              "prose": str,
              "position": int,
              "artist": str,
              "release_title": str,
            }, ...
          ],
        }
    """
    title = _esc(issue["title"])
    editor_note = _esc(issue["editor_note"])
    issue_number = int(issue["issue_number"])
    publish_date = _esc(str(issue["publish_date"]))
    padded_number = f"{issue_number:02d}"

    # Withheld picks land in the Friday surprise email — strip them here.
    rec_blocks = "\n".join(
        _render_recommendation(rec)
        for rec in issue.get("recommendations", [])
        if rec.get("category") != "withheld"
    )

    return f"""<mjml>
  <mj-head>
    <mj-title>Crate Digger — Issue {issue_number}: {title}</mj-title>
    <mj-attributes>
      <mj-all font-family="Spectral, Georgia, serif" />
    </mj-attributes>
    <mj-style>
      body {{ background: #EFE4CC; }}
      .wordmark {{ font-family: 'Instrument Serif', serif; font-style: italic; font-size: 64px; color: #15191D; }}
      .eyebrow  {{ font-family: 'DM Mono', monospace; font-size: 10px; letter-spacing: 0.28em; text-transform: uppercase; color: #C8412B; }}
      .display  {{ font-family: 'Instrument Serif', serif; font-style: italic; font-size: 96px; color: #15191D; line-height: 0.85; }}
      .editor   {{ font-family: 'Spectral', serif; font-size: 16px; line-height: 1.65; color: #2D353A; font-style: italic; }}
    </mj-style>
  </mj-head>
  <mj-body background-color="#EFE4CC">
    <mj-section padding="40px 24px 12px">
      <mj-column>
        <mj-text align="center" css-class="wordmark">
          Crate Digger<span style="color:#C8412B; font-size:0.55em;">.</span>
        </mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="24px 24px 12px">
      <mj-column>
        <mj-text align="center" css-class="eyebrow">— Issue {issue_number} · {publish_date} —</mj-text>
        <mj-text align="center" css-class="display">{padded_number}</mj-text>
        <mj-text align="center" font-style="italic" font-size="18px" color="#2D353A">{title}</mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="40px 24px 24px">
      <mj-column>
        <mj-text css-class="editor">{editor_note}</mj-text>
        <mj-text align="right" font-family="Caveat, cursive" font-size="28px" color="#C8412B">— C.</mj-text>
      </mj-column>
    </mj-section>

{rec_blocks}

    <mj-section padding="40px 24px">
      <mj-column>
        <mj-divider border-color="#15191D" border-width="2px" />
        <mj-text align="center" font-family="Caveat, cursive" font-size="32px" color="#C8412B" padding-top="20px">
          until next Sunday ♥
        </mj-text>
      </mj-column>
    </mj-section>
  </mj-body>
</mjml>"""


def _render_recommendation(rec: dict[str, Any]) -> str:
    category = _esc(rec.get("category") or "")
    source_attr = _esc(rec.get("source_attr") or "")
    artist = _esc(rec.get("artist") or "")
    release_title = _esc(rec.get("release_title") or "")
    prose = _esc(rec.get("prose") or "")

    return f"""    <mj-section padding="32px 24px" border-top="1px solid rgba(21,25,29,0.16)">
      <mj-column>
        <mj-text font-family="DM Mono, monospace" font-size="9.5px" letter-spacing="0.18em" text-transform="uppercase" color="#1E4543">
          {category} · via {source_attr}
        </mj-text>
        <mj-text font-family="DM Mono, monospace" font-size="11px" letter-spacing="0.22em" text-transform="uppercase" color="#1E4543" padding-top="4px">
          {artist}
        </mj-text>
        <mj-text font-family="Instrument Serif, serif" font-style="italic" font-size="40px" color="#15191D" padding-top="6px">
          {release_title}
        </mj-text>
        <mj-text css-class="editor" padding-top="14px">{prose}</mj-text>
      </mj-column>
    </mj-section>"""


def build_friday_drop_mjml(drop: dict[str, Any]) -> str:
    """Build the MJML for a Friday surprise drop email.

    Smaller and more intimate than Sunday's digest — one record, no editor's
    note, simpler wordmark. The drop is the surface; the prose is the body.

    Expected shape:
        {
          "issue_number": int,        # the Sunday issue this drop accompanies
          "artist": str,
          "release_title": str,
          "source_attr": str,
          "prose": str,
        }
    """
    issue_number = int(drop["issue_number"])
    artist = _esc(drop.get("artist") or "")
    release_title = _esc(drop.get("release_title") or "")
    source_attr = _esc(drop.get("source_attr") or "")
    prose = _esc(drop.get("prose") or "")

    return f"""<mjml>
  <mj-head>
    <mj-title>Crate Digger — Friday drop: {artist}</mj-title>
    <mj-attributes>
      <mj-all font-family="Spectral, Georgia, serif" />
    </mj-attributes>
    <mj-style>
      body {{ background: #EFE4CC; }}
      .wordmark {{ font-family: 'Instrument Serif', serif; font-style: italic; font-size: 48px; color: #15191D; }}
      .eyebrow  {{ font-family: 'DM Mono', monospace; font-size: 10px; letter-spacing: 0.32em; text-transform: uppercase; color: #C8412B; }}
      .meta     {{ font-family: 'DM Mono', monospace; font-size: 11px; letter-spacing: 0.22em; text-transform: uppercase; color: #1E4543; }}
      .display  {{ font-family: 'Instrument Serif', serif; font-style: italic; font-size: 56px; color: #15191D; line-height: 0.95; }}
      .editor   {{ font-family: 'Spectral', serif; font-size: 16px; line-height: 1.65; color: #2D353A; font-style: italic; }}
    </mj-style>
  </mj-head>
  <mj-body background-color="#EFE4CC">
    <mj-section padding="40px 24px 8px">
      <mj-column>
        <mj-text align="center" css-class="wordmark">
          Crate Digger<span style="color:#C8412B; font-size:0.55em;">.</span>
        </mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="16px 24px 4px">
      <mj-column>
        <mj-text align="center" css-class="eyebrow">— Friday drop · From Issue {issue_number} —</mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="24px 24px 8px">
      <mj-column>
        <mj-text css-class="meta">via {source_attr}</mj-text>
        <mj-text css-class="meta" padding-top="4px">{artist}</mj-text>
        <mj-text css-class="display" padding-top="6px">{release_title}</mj-text>
        <mj-text css-class="editor" padding-top="20px">{prose}</mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="40px 24px 24px">
      <mj-column>
        <mj-divider border-color="#15191D" border-width="2px" />
        <mj-text align="center" font-family="Caveat, cursive" font-size="28px" color="#C8412B" padding-top="20px">
          until Sunday ♥
        </mj-text>
      </mj-column>
    </mj-section>
  </mj-body>
</mjml>"""


def _esc(s: str) -> str:
    """HTML-escape user-supplied strings inside the MJML payload.

    MJML is XML-shaped — unescaped `<`, `&` etc. in editor_note or prose
    would break the parser. html.escape covers the common cases plus
    handling double quotes for attribute-safe insertion.
    """
    return html.escape(str(s), quote=True)
