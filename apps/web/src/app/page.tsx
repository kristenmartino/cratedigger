/**
 * Landing / latest-issue redirect.
 *
 * Behavior:
 *   - Not signed in       → show wordmark + invitation to sign in
 *   - Signed in, no profile → redirect to /onboarding (taste-seed capture)
 *   - Signed in, has profile → show wordmark + "your first issue ships
 *     Sunday" copy (will swap to /issue/[latest] redirect once N≥1 exists)
 */
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";
import { eq } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";
import { Wordmark } from "@/components/Wordmark";
import { AuthButtons } from "@/components/AuthButtons";

const clerkPk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const clerkEnabled = !!clerkPk && clerkPk.startsWith("pk_");

export default async function HomePage() {
  if (clerkEnabled) {
    const { userId } = await auth();
    if (userId) {
      // Look up our DB user row + whether their taste profile exists.
      // If signed in but no profile → bounce to /onboarding.
      const rows = await db
        .select({
          userId: schema.users.id,
          hasProfile: schema.tasteProfiles.id,
        })
        .from(schema.users)
        .leftJoin(
          schema.tasteProfiles,
          eq(schema.tasteProfiles.userId, schema.users.id),
        )
        .where(eq(schema.users.clerkId, userId))
        .limit(1);
      const row = rows[0];
      // Webhook hasn't fired yet OR taste profile not set up.
      if (!row || !row.hasProfile) {
        redirect("/onboarding");
      }
    }
  }

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
