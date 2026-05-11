"""Python MJML template for the Sunday issue email.

Ported from apps/email/src/templates/issue.mjml.ts so the agent can render
emails without shelling out to Node. Visual structure stays in sync with
the TS template (same wordmark, same per-record block shape, same closing
sign-off) — if you tweak one, mirror the other or both will drift.

The renderer is mjml-python (`mjml_to_html`), already in requirements.txt
at v1.4.0. The agent's render_email_node calls build_issue_mjml() and
hands the resulting string to mjml_to_html.

This template intentionally drops the withheld pick — that one ships in
Friday's surprise email, not Sunday's.
"""
from __future__ import annotations

import html
from typing import Any


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


def _esc(s: str) -> str:
    """HTML-escape user-supplied strings inside the MJML payload.

    MJML is XML-shaped — unescaped `<`, `&` etc. in editor_note or prose
    would break the parser. html.escape covers the common cases plus
    handling double quotes for attribute-safe insertion.
    """
    return html.escape(str(s), quote=True)
