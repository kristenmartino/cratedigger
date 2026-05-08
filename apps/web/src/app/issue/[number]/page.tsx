/**
 * `/issue/[number]` — the editorial digest surface.
 *
 * Pixel-level reference: docs/mockups/cratedigger-newsletter.html
 * Tokens: docs/DESIGN_SYSTEM.md
 *
 * This is a stub that fetches the issue and renders a minimal version. The
 * full layout (system-strip, masthead, signal-blocks, matched-signals,
 * pull-quote, sticky track-nav, now-digging widget, withheld seal) lands in
 * Sprint Week 5 per docs/SPRINT_PLAN.md.
 *
 * Server Component (RSC). Reads from the DB directly via @cratedigger/db.
 */
import { db, schema } from "@cratedigger/db";
import { eq } from "drizzle-orm";

type PageProps = {
  params: Promise<{ number: string }>;
};

export default async function IssuePage({ params }: PageProps) {
  const { number } = await params;
  const issueNumber = parseInt(number, 10);

  if (Number.isNaN(issueNumber) || issueNumber <= 0) {
    return (
      <div className="max-w-2xl mx-auto py-32 px-6 text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral">
          — Issue not found —
        </p>
      </div>
    );
  }

  const issue = await db.query.issues
    .findFirst({
      where: eq(schema.issues.issueNumber, issueNumber),
    })
    .catch(() => null);

  if (!issue) {
    return (
      <div className="max-w-2xl mx-auto py-32 px-6 text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral">
          — Issue {issueNumber} hasn&rsquo;t shipped yet —
        </p>
        <p className="mt-6 font-body italic text-ink-soft">
          The agent is still digging. Check back Sunday.
        </p>
      </div>
    );
  }

  return (
    <article className="max-w-3xl mx-auto py-24 px-6">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral text-center">
        — Issue —
      </p>
      <h1 className="font-display italic text-issue-display text-center mt-6">
        {String(issue.issueNumber).padStart(2, "0")}
      </h1>
      <p className="font-display italic text-[21px] text-ink-soft text-center mt-6">
        {issue.title}
      </p>
      <p className="mt-12 max-w-prose mx-auto font-body text-editor-note text-ink-soft">
        {issue.editorNote}
      </p>
      <p className="mt-12 text-center font-mono text-[10px] tracking-[0.2em] uppercase text-ink-faint">
        Full editorial layout lands Sprint Week 5
      </p>
    </article>
  );
}
