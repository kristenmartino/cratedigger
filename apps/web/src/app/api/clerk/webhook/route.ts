/**
 * Clerk webhook → auto-create / update users in Neon.
 *
 * Clerk fires this on `user.created`, `user.updated`, and `user.deleted`
 * via Svix. We verify the Svix signature, upsert the corresponding
 * `users` row, and return 200. A user without a `taste_profiles` row
 * is created here BUT is not yet eligible for Sunday issues — they
 * need to finish /onboarding first (which writes the taste profile).
 *
 * Required env:
 *   CLERK_WEBHOOK_SECRET — the signing secret from Clerk dashboard
 *
 * To wire up in Clerk:
 *   1. Dashboard → Webhooks → Add endpoint
 *   2. URL: https://cratedigger.kristenmartino.ai/api/clerk/webhook
 *   3. Subscribe to user.created, user.updated, user.deleted
 *   4. Copy the signing secret → set CLERK_WEBHOOK_SECRET in Vercel env
 *
 * Failure modes:
 *   - Missing or invalid signature → 401 (don't process forged events)
 *   - Missing CLERK_WEBHOOK_SECRET env → 500
 *   - DB write fails → log + 500 so Clerk retries (Svix delivers ~5
 *     retries with backoff on non-2xx)
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { Webhook } from "svix";
import { db, schema } from "@cratedigger/db";
import { eq, sql } from "drizzle-orm";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface ClerkEmailAddress {
  id: string;
  email_address: string;
}

interface ClerkUserData {
  id: string;
  email_addresses?: ClerkEmailAddress[];
  primary_email_address_id?: string | null;
}

interface ClerkWebhookEvent {
  type: string;
  data: ClerkUserData;
}

function primaryEmail(data: ClerkUserData): string | null {
  const addrs = data.email_addresses ?? [];
  if (addrs.length === 0) return null;
  const primary =
    data.primary_email_address_id &&
    addrs.find((a) => a.id === data.primary_email_address_id);
  return (primary || addrs[0])?.email_address ?? null;
}

export async function POST(req: NextRequest) {
  const secret = process.env.CLERK_WEBHOOK_SECRET;
  if (!secret) {
    console.error("CLERK_WEBHOOK_SECRET not configured");
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }

  // Svix headers — set by Clerk on every delivery
  const svixId = req.headers.get("svix-id");
  const svixTimestamp = req.headers.get("svix-timestamp");
  const svixSignature = req.headers.get("svix-signature");
  if (!svixId || !svixTimestamp || !svixSignature) {
    return NextResponse.json({ error: "Missing Svix headers" }, { status: 401 });
  }

  const body = await req.text();
  const wh = new Webhook(secret);

  let evt: ClerkWebhookEvent;
  try {
    evt = wh.verify(body, {
      "svix-id": svixId,
      "svix-timestamp": svixTimestamp,
      "svix-signature": svixSignature,
    }) as ClerkWebhookEvent;
  } catch (e) {
    console.warn("Clerk webhook signature verify failed:", e);
    return NextResponse.json({ error: "Invalid signature" }, { status: 401 });
  }

  const data = evt.data;
  if (!data?.id) {
    return NextResponse.json({ error: "Missing user id" }, { status: 400 });
  }

  try {
    if (evt.type === "user.created" || evt.type === "user.updated") {
      const email = primaryEmail(data);
      if (!email) {
        // Clerk allows users without emails (OAuth-only with no scope grant
        // for email) but our schema requires email NOT NULL. We can't
        // create the row yet; Clerk will replay on user.updated once
        // email lands.
        console.warn(
          `Clerk ${evt.type} for ${data.id} had no email — skipping`,
        );
        return NextResponse.json({ skipped: "no_email" });
      }

      // Idempotent upsert keyed by clerk_id. ON CONFLICT updates email so
      // user.updated events that change the primary email get reflected.
      await db
        .insert(schema.users)
        .values({ clerkId: data.id, email })
        .onConflictDoUpdate({
          target: schema.users.clerkId,
          set: { email: sql`EXCLUDED.email` },
        });

      console.info(`Clerk ${evt.type}: upserted user ${data.id}`);
      return NextResponse.json({ ok: true });
    }

    if (evt.type === "user.deleted") {
      // Soft note: cascading deletes drop issues, recommendations,
      // taste_profiles, feedback for this user (FKs declared with
      // ON DELETE CASCADE in init.sql). If a user is recreated under
      // the same clerk_id later, they start fresh — that's fine.
      await db
        .delete(schema.users)
        .where(eq(schema.users.clerkId, data.id));
      console.info(`Clerk user.deleted: removed user ${data.id}`);
      return NextResponse.json({ ok: true });
    }

    // Other event types we subscribe to but don't act on
    return NextResponse.json({ ignored: evt.type });
  } catch (e) {
    console.error("Clerk webhook handler error:", e);
    return NextResponse.json({ error: "DB write failed" }, { status: 500 });
  }
}
