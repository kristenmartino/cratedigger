/**
 * MJML template builder for an Issue.
 *
 * Stub — emits a minimal email shell. Full editorial layout (system-strip,
 * lead spread, signal-blocks, matched-signals, withheld seal) lands Sprint
 * Week 6. Until then this is just enough to round-trip an Issue → HTML so
 * the agent's `render_email` node has something to call.
 */
import type { Issue } from "@cratedigger/shared";

export function buildIssueTemplate(issue: Issue): string {
  const title = escapeXml(issue.title);
  const editorNote = escapeXml(issue.editorNote);

  return `<mjml>
  <mj-head>
    <mj-title>Crate Digger — Issue ${issue.issueNumber}: ${title}</mj-title>
    <mj-attributes>
      <mj-all font-family="Spectral, Georgia, serif" />
    </mj-attributes>
    <mj-style>
      body { background: #EFE4CC; }
      .wordmark { font-family: 'Instrument Serif', serif; font-style: italic; font-size: 64px; color: #15191D; }
      .eyebrow  { font-family: 'DM Mono', monospace; font-size: 10px; letter-spacing: 0.28em; text-transform: uppercase; color: #C8412B; }
      .display  { font-family: 'Instrument Serif', serif; font-style: italic; font-size: 96px; color: #15191D; line-height: 0.85; }
      .editor   { font-family: 'Spectral', serif; font-size: 16px; line-height: 1.65; color: #2D353A; font-style: italic; }
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
        <mj-text align="center" css-class="eyebrow">— Issue ${issue.issueNumber} · ${issue.publishDate} —</mj-text>
        <mj-text align="center" css-class="display">${String(issue.issueNumber).padStart(2, "0")}</mj-text>
        <mj-text align="center" font-style="italic" font-size="18px" color="#2D353A">${title}</mj-text>
      </mj-column>
    </mj-section>

    <mj-section padding="40px 24px 24px">
      <mj-column>
        <mj-text css-class="editor">${editorNote}</mj-text>
        <mj-text align="right" font-family="Caveat, cursive" font-size="28px" color="#C8412B">— C.</mj-text>
      </mj-column>
    </mj-section>

    ${issue.recommendations
      .filter((r) => r.category !== "withheld")
      .map(renderRecommendation)
      .join("\n")}

    <mj-section padding="40px 24px">
      <mj-column>
        <mj-divider border-color="#15191D" border-width="2px" />
        <mj-text align="center" font-family="Caveat, cursive" font-size="32px" color="#C8412B" padding-top="20px">
          until next Sunday ♥
        </mj-text>
      </mj-column>
    </mj-section>
  </mj-body>
</mjml>`;
}

function renderRecommendation(rec: Issue["recommendations"][number]): string {
  const release = rec.release;
  return `
    <mj-section padding="32px 24px" border-top="1px solid rgba(21,25,29,0.16)">
      <mj-column>
        <mj-text font-family="DM Mono, monospace" font-size="9.5px" letter-spacing="0.18em" text-transform="uppercase" color="#1E4543">
          ${escapeXml(rec.category)} · via ${escapeXml(rec.sourceAttr)}
        </mj-text>
        <mj-text font-family="DM Mono, monospace" font-size="11px" letter-spacing="0.22em" text-transform="uppercase" color="#1E4543" padding-top="4px">
          ${escapeXml(release.artist)}
        </mj-text>
        <mj-text font-family="Instrument Serif, serif" font-style="italic" font-size="40px" color="#15191D" padding-top="6px">
          ${escapeXml(release.title)}
        </mj-text>
        <mj-text css-class="editor" padding-top="14px">${escapeXml(rec.prose)}</mj-text>
      </mj-column>
    </mj-section>`;
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
