/**
 * Vercel cron → trigger the weekly Crate Digger pipeline.
 *
 * Schedule lives in vercel.json. Vercel cron requests carry an
 * `Authorization: Bearer <CRON_SECRET>` header that we verify before
 * forwarding to the Railway agent service. The agent service then
 * verifies its own `X-Pipeline-Key` header before kicking off the
 * LangGraph pipeline.
 *
 * v1 single-user: enumerates all users that have a taste profile and
 * triggers one issue generation per user. Kristen is the only one
 * for now; v1.1 invitees plug in here automatically.
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";

// Need Node.js runtime (postgres driver, not Edge-compatible)
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8001";

export async function POST(req: NextRequest) {
  // Vercel cron auth — sends Authorization: Bearer <CRON_SECRET> automatically
  const expected = process.env.CRON_SECRET;
  if (!expected) {
    console.error("CRON_SECRET not configured");
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }
  const auth = req.headers.get("authorization");
  if (auth !== `Bearer ${expected}`) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const pipelineKey = process.env.PIPELINE_API_KEY;
  if (!pipelineKey) {
    console.error("PIPELINE_API_KEY not configured");
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }

  // Find all users that have a taste profile (i.e. eligible for an issue)
  const users = await db
    .select({ id: schema.users.id, email: schema.users.email })
    .from(schema.users)
    .innerJoin(
      schema.tasteProfiles,
      eq(schema.users.id, schema.tasteProfiles.userId),
    );

  const triggered: Array<{
    user_id: string;
    email: string;
    agent_run_id?: string;
    error?: string;
  }> = [];

  for (const user of users) {
    try {
      const res = await fetch(`${AGENT_URL}/v1/run-issue`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Pipeline-Key": pipelineKey,
        },
        body: JSON.stringify({ user_id: user.id }),
      });
      const json = (await res.json()) as { agent_run_id?: string };
      triggered.push({
        user_id: user.id,
        email: user.email,
        agent_run_id: json.agent_run_id,
      });
    } catch (e) {
      triggered.push({
        user_id: user.id,
        email: user.email,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return NextResponse.json({ count: triggered.length, triggered });
}
