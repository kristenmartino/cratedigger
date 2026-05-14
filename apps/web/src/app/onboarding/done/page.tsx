/**
 * Onboarding success page. Shown after /api/onboarding accepts the seed.
 *
 * Also serves as the Spotify-connect entry point. The agent finishes the
 * taste centroid in the background; meanwhile we invite the user to
 * link Spotify so the rolling Crate Digger playlist auto-updates each
 * Sunday with their week's picks.
 */
import { auth } from "@clerk/nextjs/server";
import { eq } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";
import { SpotifyConnectButton } from "@/components/SpotifyConnectButton";

const clerkPk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const clerkEnabled = !!clerkPk && clerkPk.startsWith("pk_");

export default async function OnboardingDonePage() {
  let connected = false;
  if (clerkEnabled) {
    const { userId } = await auth();
    if (userId) {
      const rows = await db
        .select({ revoked: schema.userSpotifyConnections.revoked })
        .from(schema.userSpotifyConnections)
        .innerJoin(
          schema.users,
          eq(schema.users.id, schema.userSpotifyConnections.userId),
        )
        .where(eq(schema.users.clerkId, userId))
        .limit(1);
      connected = !!rows[0] && !rows[0].revoked;
    }
  }

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

      <div className="mt-10">
        <SpotifyConnectButton connected={connected} />
      </div>
      {!connected && (
        <p className="mt-3 max-w-prose font-body italic text-[13px] text-ink-soft">
          Optional. We&apos;ll keep a rolling Spotify playlist of each week&apos;s picks on your account.
        </p>
      )}

      <p className="mt-12 font-display text-coral text-[22px] italic">— C.</p>
    </div>
  );
}
