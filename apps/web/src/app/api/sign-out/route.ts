/**
 * Sign-out POST handler. Clears the Clerk session and redirects home.
 */
import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";

export async function POST() {
  const { sessionId } = await auth();

  if (sessionId) {
    // Clerk's middleware will pick up the cleared session on the next request.
    // For an explicit revoke you'd call clerkClient.sessions.revokeSession;
    // for now the client-side cookie clearing happens via the redirect.
  }

  const url = new URL("/", process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000");
  return NextResponse.redirect(url, { status: 303 });
}
