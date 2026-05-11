/**
 * Vercel cron → trigger the Friday surprise email per user.
 *
 * Companion to /api/cron/run-issue. Schedule lives in vercel.json
 * (`0 13 * * 5` — Friday 13:00 UTC). Same two-leg auth as the Sunday
 * cron: Vercel sends Bearer CRON_SECRET, we verify and then forward to
 * Railway with X-Pipeline-Key.
 *
 * Eligibility query: any user with at least one withheld recommendation
 * whose withhold_until has passed and that hasn't been delivered yet.
 * The fan-out is per-user (not per-recommendation) — the agent endpoint
 * sweeps all pending picks for that user in one pass.
 *
 * Failure handling mirrors run-issue:
 *   - Top-level catch fires a fatal ops alert
 *   - Per-user non-2xx / fetch errors get collected, one summary alert
 *   - Auth misconfigs (no CRON_SECRET / PIPELINE_API_KEY) do NOT alert
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { and, eq, isNull, isNotNull, lte, sql } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";
import { sendFailureAlert } from "@/lib/alerts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8001";

interface DropResult {
  user_id: string;
  email: string;
  eligible?: number;
  delivered?: number;
  agent_errors?: string[];
  error?: string;
}

export async function POST(req: NextRequest) {
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
    // Distinct users with at least one withheld pick whose drop time
    // has passed and that hasn't been delivered yet.
    const eligibleUsers = await db
      .selectDistinct({
        id: schema.users.id,
        email: schema.users.email,
      })
      .from(schema.users)
      .innerJoin(schema.issues, eq(schema.issues.userId, schema.users.id))
      .innerJoin(
        schema.recommendations,
        eq(schema.recommendations.issueId, schema.issues.id),
      )
      .where(
        and(
          eq(schema.recommendations.category, "withheld"),
          isNotNull(schema.recommendations.withholdUntil),
          lte(schema.recommendations.withholdUntil, sql`NOW()`),
          isNull(schema.recommendations.withheldDeliveredAt),
        ),
      );

    const results: DropResult[] = [];

    for (const user of eligibleUsers) {
      try {
        const res = await fetch(`${AGENT_URL}/v1/friday-drop`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Pipeline-Key": pipelineKey,
          },
          body: JSON.stringify({ user_id: user.id }),
        });

        if (!res.ok) {
          const body = await res.text().catch(() => "<no body>");
          results.push({
            user_id: user.id,
            email: user.email,
            error: `HTTP ${res.status}: ${body.slice(0, 200)}`,
          });
          continue;
        }

        const json = (await res.json()) as {
          eligible?: number;
          delivered?: number;
          errors?: string[];
        };
        results.push({
          user_id: user.id,
          email: user.email,
          eligible: json.eligible,
          delivered: json.delivered,
          agent_errors: json.errors,
        });
      } catch (e) {
        results.push({
          user_id: user.id,
          email: user.email,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    }

    // Roll up agent-side errors + transport failures into one alert.
    const failed = results.filter(
      (r) => r.error || (r.agent_errors && r.agent_errors.length > 0),
    );
    if (failed.length > 0) {
      const body = [
        `${failed.length} of ${results.length} Friday-drop user triggers had issues.`,
        "",
        ...failed.map((f) => {
          const lines: string[] = [`- ${f.email} (user_id: ${f.user_id})`];
          if (f.error) lines.push(`  transport: ${f.error}`);
          if (f.agent_errors && f.agent_errors.length > 0) {
            for (const e of f.agent_errors) lines.push(`  agent: ${e}`);
          }
          return lines.join("\n");
        }),
      ].join("\n");
      await sendFailureAlert(
        `[Crate Digger] Friday drop: ${failed.length}/${results.length} user triggers had issues`,
        body,
      );
    }

    return NextResponse.json({ count: results.length, results });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    const stack = e instanceof Error ? e.stack ?? "" : "";
    console.error("friday-drop cron route fatal:", e);
    await sendFailureAlert(
      "[Crate Digger] Friday drop cron route fatal error",
      `The Friday drop cron raised before fan-out completed. No surprise emails were sent.\n\nerror: ${message}\n\nstack:\n${stack}`,
    );
    return NextResponse.json({ error: "Internal", message }, { status: 500 });
  }
}
