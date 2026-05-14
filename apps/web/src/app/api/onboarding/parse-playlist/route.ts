/**
 * Onboarding helper — proxy to the agent's playlist parser.
 *
 * The agent service holds the Spotify credentials, so we don't duplicate
 * them on Vercel. The web app passes through the user's URL with the
 * pipeline key as auth. The agent returns the unique artist list (or an
 * empty list if the playlist is private / not found / Spotify is down).
 *
 * Auth: requires a signed-in Clerk session. The endpoint isn't sensitive
 * (it just reads public Spotify data) but gating on auth prevents random
 * unauthenticated callers from burning our Spotify rate-limit budget.
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { z } from "zod";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ParseSchema = z.object({
  playlist_url: z.string().trim().min(1).max(500),
});

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8001";

export async function POST(req: NextRequest) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const parsed = ParseSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "playlist_url required" },
      { status: 400 },
    );
  }

  const pipelineKey = process.env.PIPELINE_API_KEY;
  if (!pipelineKey) {
    return NextResponse.json(
      { error: "Server misconfigured" },
      { status: 500 },
    );
  }

  try {
    const res = await fetch(`${AGENT_URL}/v1/parse-playlist`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pipeline-Key": pipelineKey,
      },
      body: JSON.stringify({ playlist_url: parsed.data.playlist_url }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return NextResponse.json(
        { error: text || `Agent returned ${res.status}` },
        { status: res.status },
      );
    }
    const payload = (await res.json()) as {
      playlist_id: string;
      artists: string[];
    };
    return NextResponse.json(payload);
  } catch (e) {
    console.error("parse-playlist proxy error:", e);
    return NextResponse.json({ error: "Agent unreachable" }, { status: 502 });
  }
}
