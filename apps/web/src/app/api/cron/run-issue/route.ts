/**
 * Vercel cron → trigger the weekly Crate Digger pipeline.
 *
 * Schedule lives in vercel.json. Vercel cron issues a GET carrying an
 * `Authorization: Bearer <CRON_SECRET>` header that we verify before
 * forwarding to the Railway agent service. The agent service then
 * verifies its own `X-Pipeline-Key` header before kicking off the
 * LangGraph pipeline.
 *
 * v1 single-user: enumerates all users that have a taste profile and
 * triggers one issue generation per user. Kristen is the only one
 * for now; v1.1 invitees plug in here automatically.
 *
 * Failure modes that fire an ops alert (Resend → OPS_ALERT_EMAIL):
 *   1. Top-level exception (DB unreachable, bad env, etc.) — alert + 500
 *   2. Per-user trigger error (Railway 5xx, fetch reject, non-2xx) —
 *      collected, alerted once as a batch summary so we don't email
 *      N times for the same root cause
 *
 * Auth failures (wrong CRON_SECRET, missing env) do NOT alert — those
 * are configuration issues, not pipeline failures, and Vercel surfaces
 * them in deployment logs already.
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";
import { sendFailureAlert } from "@/lib/alerts";

// Need Node.js runtime (postgres driver, not Edge-compatible)
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8001";

interface TriggerResult {
  user_id: string;
  email: string;
  agent_run_id?: string;
  error?: string;
}

async function handler(req: NextRequest) {
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

  try {
    // Find all users that have a taste profile (i.e. eligible for an issue)
    const users = await db
      .select({ id: schema.users.id, email: schema.users.email })
      .from(schema.users)
      .innerJoin(
        schema.tasteProfiles,
        eq(schema.users.id, schema.tasteProfiles.userId),
      );

    const triggered: TriggerResult[] = [];

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

        if (!res.ok) {
          const body = await res.text().catch(() => "<no body>");
          triggered.push({
            user_id: user.id,
            email: user.email,
            error: `HTTP ${res.status}: ${body.slice(0, 200)}`,
          });
          continue;
        }

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

    // Per-user failure summary alert. One email no matter how many users
    // failed — we want a heads-up, not an inbox flood.
    const failed = triggered.filter((t) => t.error);
    if (failed.length > 0) {
      const body = [
        `${failed.length} of ${triggered.length} user triggers failed.`,
        "",
        ...failed.map(
          (f) => `- ${f.email} (user_id: ${f.user_id})\n  ${f.error}`,
        ),
      ].join("\n");
      await sendFailureAlert(
        `[Crate Digger] Sunday cron: ${failed.length}/${triggered.length} user triggers failed`,
        body,
      );
    }

    return NextResponse.json({ count: triggered.length, triggered });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    const stack = e instanceof Error ? e.stack ?? "" : "";
    console.error("cron route fatal:", e);
    await sendFailureAlert(
      "[Crate Digger] Sunday cron route fatal error",
      `The cron route raised before fan-out completed. The pipeline did NOT run.\n\nerror: ${message}\n\nstack:\n${stack}`,
    );
    return NextResponse.json(
      { error: "Internal", message },
      { status: 500 },
    );
  }
}

// Vercel Cron invokes the scheduled path with GET, so a POST-only route
// silently 405s every week and the pipeline never runs. POST stays exported
// for manual triggering.
export const GET = handler;
export const POST = handler;
