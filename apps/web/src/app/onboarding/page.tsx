/**
 * Onboarding — taste-seed capture for new signups.
 *
 * Server component routing:
 *   - Not signed in → /sign-in
 *   - Already has taste profile → /
 *   - User row missing (webhook race) → render form anyway; the
 *     /api/onboarding handler upserts by clerk_id and creates the row
 *     if Clerk's webhook hasn't landed yet.
 *
 * Once submitted, /api/onboarding writes the seed to taste_profiles.seed
 * and fires the agent service to compute embeddings + tag weights in the
 * background. The user gets routed to /onboarding/done immediately.
 */
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";
import { eq } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";
import { OnboardingForm } from "./OnboardingForm";

const clerkPk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const clerkEnabled = !!clerkPk && clerkPk.startsWith("pk_");

export default async function OnboardingPage() {
  if (!clerkEnabled) {
    // If Clerk isn't set up locally, we can't gate. Show the form anyway
    // for dev iteration — submissions will 500 against /api/onboarding.
    return <OnboardingShell />;
  }

  const { userId } = await auth();
  if (!userId) redirect("/sign-in?redirect_url=/onboarding");

  const rows = await db
    .select({ hasProfile: schema.tasteProfiles.id })
    .from(schema.users)
    .leftJoin(
      schema.tasteProfiles,
      eq(schema.tasteProfiles.userId, schema.users.id),
    )
    .where(eq(schema.users.clerkId, userId))
    .limit(1);
  if (rows[0]?.hasProfile) redirect("/");

  return <OnboardingShell />;
}

function OnboardingShell() {
  return (
    <div className="min-h-screen px-6 py-16 max-w-2xl mx-auto">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral text-center">
        — Set up your taste —
      </p>

      <h1 className="mt-6 font-display italic text-issue-display text-center">
        Welcome.
      </h1>

      <div className="mt-10 font-body text-[15px] leading-relaxed text-ink-soft space-y-4 max-w-prose mx-auto">
        <p>
          Paste a Spotify playlist you actually listen to, or just drop the
          artists in by hand. Twenty or so is plenty. We use them to find
          adjacent music worth your time.
        </p>
        <p>
          Then pick the genres that feel honest. You can refine all of this
          later.
        </p>
      </div>

      <div className="mt-12">
        <OnboardingForm />
      </div>
    </div>
  );
}
