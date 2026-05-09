/**
 * Landing / latest-issue redirect.
 *
 * Per SPEC.md §6 the root should redirect to `/issue/[latest]`. Until the DB
 * has issues, we render a simple welcome with the wordmark.
 *
 * Once the seed script has run, replace this with:
 *   redirect(`/issue/${latestIssueNumber}`);
 */
import { Wordmark } from "@/components/Wordmark";
import { AuthButtons } from "@/components/AuthButtons";

export default function HomePage() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 text-center relative">
      <div className="absolute top-6 right-6">
        <AuthButtons />
      </div>

      <Wordmark />

      <p className="mt-5 font-display italic text-[17px] text-ink-soft">
        a weekly dispatch of music worth digging for
      </p>

      <p className="mt-12 font-mono text-[10px] uppercase tracking-[0.28em] text-coral">
        — Issue 04 · Sunday, 10 May 2026 —
      </p>

      <p className="mt-6 max-w-xl font-body text-[15px] leading-relaxed text-ink-soft italic">
        The first issue is being prepared. Sign in to subscribe and be among
        the first to receive Crate Digger when it ships.
      </p>
    </div>
  );
}
