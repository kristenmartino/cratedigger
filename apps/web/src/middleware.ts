/**
 * Clerk middleware. Harvested from sift/middleware.ts.
 *
 * Differences from Sift:
 *   - PROTECTED_PREFIXES updated for CD's auth-required routes (feedback,
 *     annotations, taste profile management, withheld surprise reveal).
 */
import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const clerkPk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const clerkEnabled = !!clerkPk && clerkPk.startsWith("pk_");

const clerk = clerkEnabled ? clerkMiddleware() : undefined;

// Paths that require auth — fail-closed if Clerk is misconfigured
const PROTECTED_PREFIXES = [
  "/api/feedback",
  "/api/annotations",
  "/api/taste-profile",
  "/api/internal", // pipeline internal triggers
];

export default function middleware(request: NextRequest) {
  if (clerk) return clerk(request, {} as any);

  // Fail-closed: block protected API routes when Clerk is not configured
  const path = request.nextUrl.pathname;
  if (PROTECTED_PREFIXES.some((p) => path.startsWith(p))) {
    console.error("Clerk is not configured — blocking protected route:", path);
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
