/**
 * Onboarding success page. Shown after /api/onboarding accepts the seed.
 *
 * No DB calls — the agent is still working in the background. The next
 * Sunday cron picks up this user automatically (cron route enumerates
 * users WHERE EXISTS taste_profile).
 */
export default function OnboardingDonePage() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 text-center">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral">
        — You&apos;re in —
      </p>

      <h1 className="mt-6 font-display italic text-issue-display">Welcome.</h1>

      <p className="mt-10 max-w-prose font-body text-[15px] leading-relaxed text-ink-soft italic">
        We&apos;re tuning to your taste. Your first issue lands in your inbox
        Sunday morning — five records worth digging for, prose-not-marketing,
        no algorithm gunk.
      </p>

      <p className="mt-8 font-display text-coral text-[22px] italic">— C.</p>
    </div>
  );
}
