/**
 * Spotify OAuth callback — exchanges code for tokens, persists to DB.
 *
 * Validates:
 *   - the state cookie matches the `state` query param (CSRF protection)
 *   - the Clerk session is present (so we know which user_id to attach)
 *
 * On success: upserts user_spotify_connections with the refresh_token
 * (long-lived) + an initial access_token (1-hour TTL). The cron job
 * uses the refresh_token to mint fresh access_tokens at sync time.
 *
 * Spotify returns errors at this endpoint as `?error=access_denied`
 * (user clicked deny) or other reasons — surface those back to the
 * user, don't crash.
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import { eq, sql } from "drizzle-orm";
import { db, schema } from "@cratedigger/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface SpotifyTokenResponse {
  access_token: string;
  token_type: string;
  scope: string;
  expires_in: number;
  refresh_token?: string;
}

interface SpotifyMeResponse {
  id: string;
}

export async function GET(req: NextRequest) {
  const { userId: clerkId } = await auth();
  if (!clerkId) {
    return NextResponse.redirect(new URL("/sign-in", req.url));
  }

  const url = req.nextUrl;
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const errorParam = url.searchParams.get("error");

  if (errorParam) {
    // Most common: user clicked Cancel on Spotify's authorize page.
    // Send them back to the page they came from with a flag we can
    // show a "you cancelled" message on later.
    return NextResponse.redirect(
      new URL(`/?spotify_error=${encodeURIComponent(errorParam)}`, req.url),
    );
  }

  const cookieState = req.cookies.get("spotify_oauth_state")?.value;
  if (!code || !state || !cookieState || state !== cookieState) {
    return NextResponse.json(
      { error: "Invalid OAuth state — restart the connect flow" },
      { status: 400 },
    );
  }

  const clientId = process.env.SPOTIFY_CLIENT_ID;
  const clientSecret = process.env.SPOTIFY_CLIENT_SECRET;
  const redirectUri = process.env.SPOTIFY_REDIRECT_URI;
  if (!clientId || !clientSecret || !redirectUri) {
    return NextResponse.json(
      { error: "Spotify OAuth env not configured" },
      { status: 500 },
    );
  }

  // Exchange the auth code for tokens
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const tokenRes = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
    }),
  });
  if (!tokenRes.ok) {
    const text = await tokenRes.text().catch(() => "");
    console.error("Spotify token exchange failed:", tokenRes.status, text);
    return NextResponse.json(
      { error: `Spotify token exchange failed (${tokenRes.status})` },
      { status: 502 },
    );
  }
  const tokens = (await tokenRes.json()) as SpotifyTokenResponse;
  if (!tokens.access_token || !tokens.refresh_token) {
    return NextResponse.json(
      { error: "Spotify response missing tokens" },
      { status: 502 },
    );
  }

  // Fetch the user's Spotify id — needed when we create their playlist
  const meRes = await fetch("https://api.spotify.com/v1/me", {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  });
  if (!meRes.ok) {
    const text = await meRes.text().catch(() => "");
    console.error("Spotify /me failed:", meRes.status, text);
    return NextResponse.json(
      { error: `Couldn't fetch Spotify user id (${meRes.status})` },
      { status: 502 },
    );
  }
  const me = (await meRes.json()) as SpotifyMeResponse;

  // Resolve our user_id. Defensive create in case webhook race lost
  // the user.created event.
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

  const expiresAt = new Date(Date.now() + tokens.expires_in * 1000);
  await db
    .insert(schema.userSpotifyConnections)
    .values({
      userId,
      spotifyUserId: me.id,
      refreshToken: tokens.refresh_token,
      accessToken: tokens.access_token,
      accessTokenExpiresAt: expiresAt,
      scopes: tokens.scope ? tokens.scope.split(" ") : [],
    })
    .onConflictDoUpdate({
      target: schema.userSpotifyConnections.userId,
      set: {
        spotifyUserId: me.id,
        refreshToken: tokens.refresh_token,
        accessToken: tokens.access_token,
        accessTokenExpiresAt: expiresAt,
        scopes: tokens.scope ? tokens.scope.split(" ") : [],
        revoked: false,
        lastError: null,
      },
    });

  // Clear the state cookie now that we've consumed it
  const res = NextResponse.redirect(new URL("/?spotify=connected", req.url));
  res.cookies.delete("spotify_oauth_state");
  return res;
}
