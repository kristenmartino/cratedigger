/**
 * Spotify OAuth — start the authorization flow.
 *
 * Clerk-gated. We generate a CSRF-resistant state cookie, redirect the
 * user to Spotify's authorize endpoint, and Spotify redirects them back
 * to /api/spotify/callback with the auth code. The callback validates
 * the state, exchanges the code for tokens, and persists the
 * refresh_token in user_spotify_connections.
 *
 * Scopes:
 *   - playlist-modify-public — create/update the rolling
 *     "Crate Digger" playlist on the user's account
 *   - user-read-email — minimal identity (to fetch the spotify_user_id
 *     we need for playlist creation)
 *
 * Required env:
 *   SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET (same creds used by the
 *     agent for catalog search), and:
 *   SPOTIFY_REDIRECT_URI — must be registered in the Spotify developer
 *     dashboard. For prod: https://cratedigger.kristenmartino.ai/api/spotify/callback
 */
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { randomBytes } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SCOPES = ["playlist-modify-public", "user-read-email"].join(" ");

export async function GET(req: NextRequest) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.redirect(new URL("/sign-in?redirect_url=/api/spotify/connect", req.url));
  }

  const clientId = process.env.SPOTIFY_CLIENT_ID;
  const redirectUri = process.env.SPOTIFY_REDIRECT_URI;
  if (!clientId || !redirectUri) {
    return NextResponse.json(
      { error: "Spotify OAuth env not configured" },
      { status: 500 },
    );
  }

  // Random state, validated on callback. Short TTL — the redirect round
  // trip takes a few seconds at most.
  const state = randomBytes(16).toString("hex");

  const authorizeUrl = new URL("https://accounts.spotify.com/authorize");
  authorizeUrl.searchParams.set("response_type", "code");
  authorizeUrl.searchParams.set("client_id", clientId);
  authorizeUrl.searchParams.set("scope", SCOPES);
  authorizeUrl.searchParams.set("redirect_uri", redirectUri);
  authorizeUrl.searchParams.set("state", state);
  // show_dialog=false: Spotify won't re-prompt users who already
  // authorized our app. Useful when a user re-runs /connect after
  // revoking and re-granting.

  const res = NextResponse.redirect(authorizeUrl.toString());
  // HttpOnly + SameSite=Lax cookie; safe for the OAuth round trip
  res.cookies.set({
    name: "spotify_oauth_state",
    value: state,
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/api/spotify",
    maxAge: 600, // 10 min — plenty for the redirect
  });
  return res;
}
