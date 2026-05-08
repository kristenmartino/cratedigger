/**
 * Render an Issue to HTML via MJML.
 *
 * Per CLAUDE.md "Email and web render from the same content" — this consumes
 * the same `Issue` shape from @cratedigger/shared that `/issue/[n]` reads.
 *
 * The MJML template (templates/issue.mjml.ts) emits the editorial digest
 * structurally similar to the web version but simplified for email clients:
 *   - No sticky nav
 *   - Halftone covers as inline base64 PNG (Outlook doesn't render SVG well)
 *   - "Open in browser" link at top
 *   - "Open in archive" link at bottom
 */
import mjml2html from "mjml";
import type { Issue } from "@cratedigger/shared";
import { buildIssueTemplate } from "./templates/issue.mjml";

export async function renderIssue(issue: Issue): Promise<{ html: string; errors: unknown[] }> {
  const mjmlSource = buildIssueTemplate(issue);
  // @types/mjml types mjml2html as returning Promise<MJMLParseResults>;
  // the runtime is actually synchronous in v4, but await covers both.
  const result = await mjml2html(mjmlSource, { validationLevel: "strict" });
  return { html: result.html, errors: result.errors };
}
