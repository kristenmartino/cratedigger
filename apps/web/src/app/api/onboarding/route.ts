/**
 * Onboarding submit — capture taste seed.
 *
 * Flow:
 *   1. Verify Clerk auth (must be a logged-in user)
 *   2. Validate the form payload (same limits as OnboardingForm.tsx)
 *   3. Find or create the user row by clerk_id (covers the case where
 *      Clerk's webhook hasn't landed yet — race condition between
 *      sign-up redirect and webhook delivery)
 *   4. UPSERT taste_profiles row with the seed JSONB. taste_centroid
 *      starts NULL; the agent fills it in async.
 *   5. Fire-and-forget POST to the agent service to compute centroid +
 *      tag weights. If that call fails the seed is still persisted —
 *      a retry workflow can finish the build later.
 *
 * Why the agent does the heavy lift: Voyage embeddings take 1-3s and
 * we don't want the user staring at a spinner. They get routed to
 * /onboarding/done immediately while the centroid is computed.
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import { eq, sql } from "drizzle-orm";
import { z } from "zod";
import { db, schema } from "@cratedigger/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MIN_ARTISTS = 10;
const MAX_ARTISTS = 50;
const MIN_TAGS = 3;
// Loose upper bound — the client cap matches the chip vocabulary
// (currently ~50). Picking every available genre is allowed.
const MAX_TAGS = 60;

const OnboardingSchema = z.object({
  artists: z
    .array(z.string().trim().min(1).max(120))
    .min(MIN_ARTISTS)
    .max(MAX_ARTISTS),
  tags: z
    .array(z.string().trim().min(1).max(40))
    .min(MIN_TAGS)
    .max(MAX_TAGS),
});

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8001";

export async function POST(req: NextRequest) {
  const { userId: clerkId } = await auth();
  if (!clerkId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const parsed = OnboardingSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: parsed.error.issues[0]?.message || "Validation failed" },
      { status: 400 },
    );
  }

  // Defensive: create the user row if the webhook race lost. Email
  // pulled from Clerk's session (the webhook would do the same).
  let userRows = await db
    .select({ id: schema.users.id })
    .from(schema.users)
    .where(eq(schema.users.clerkId, clerkId))
    .limit(1);

  if (userRows.length === 0) {
    const u = await currentUser();
    const email =
      u?.primaryEmailAddress?.emailAddress ??
      u?.emailAddresses?.[0]?.emailAddress;
    if (!email) {
      return NextResponse.json(
        { error: "No email on file — sign in with email and retry" },
        { status: 400 },
      );
    }
    await db
      .insert(schema.users)
      .values({ clerkId, email })
      .onConflictDoUpdate({
        target: schema.users.clerkId,
        set: { email: sql`EXCLUDED.email` },
      });
    userRows = await db
      .select({ id: schema.users.id })
      .from(schema.users)
      .where(eq(schema.users.clerkId, clerkId))
      .limit(1);
  }

  const userId = userRows[0]?.id;
  if (!userId) {
    return NextResponse.json({ error: "User row write failed" }, { status: 500 });
  }

  // Seed shape matches build_profile_from_seed's "manual" kind.
  const seed = {
    version: 1,
    kind: "manual",
    artists: parsed.data.artists,
    tags: parsed.data.tags,
  };

  // Insert/update the taste_profiles row with just the seed. centroid +
  // tag weights are filled in by the agent's POST /v1/build-taste-profile.
  await db
    .insert(schema.tasteProfiles)
    .values({
      userId,
      seed,
      tags: {},
      sourceWeights: {},
    })
    .onConflictDoUpdate({
      target: schema.tasteProfiles.userId,
      set: {
        seed,
        updatedAt: sql`NOW()`,
      },
    });

  // Fire-and-forget: kick the agent to compute the centroid. We don't
  // await — the user gets routed to /onboarding/done immediately.
  // If the agent call fails the seed is still safely persisted; a
  // retry job can finish the build later.
  const pipelineKey = process.env.PIPELINE_API_KEY;
  if (pipelineKey) {
    void fetch(`${AGENT_URL}/v1/build-taste-profile`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pipeline-Key": pipelineKey,
      },
      body: JSON.stringify({ user_id: userId }),
    }).catch((e) => {
      console.warn(`Agent build-taste-profile dispatch failed: ${e}`);
    });
  } else {
    console.warn(
      "PIPELINE_API_KEY not configured — seed persisted but centroid not built",
    );
  }

  return NextResponse.json({ ok: true });
}
