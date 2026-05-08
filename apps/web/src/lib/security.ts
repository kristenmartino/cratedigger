/**
 * CSRF check. Harvested verbatim from sift/lib/security.ts.
 *
 * Validates that mutation requests (POST, PUT, DELETE, PATCH) originate from
 * the same site by checking Sec-Fetch-Site (primary), Origin, then Referer.
 * Returns null if valid, or a 403 NextResponse if the check fails.
 */
import { NextRequest, NextResponse } from "next/server";

export function checkCsrf(request: NextRequest): NextResponse | null {
  const method = request.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") {
    return null;
  }

  const secFetchSite = request.headers.get("sec-fetch-site");
  if (secFetchSite) {
    if (secFetchSite === "same-origin" || secFetchSite === "same-site" || secFetchSite === "none") {
      return null;
    }
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const origin = request.headers.get("origin");
  const referer = request.headers.get("referer");
  const host = request.headers.get("host");

  if (!host) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  if (origin) {
    try {
      const originHost = new URL(origin).host;
      if (originHost === host) return null;
    } catch {
      // invalid origin
    }
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  if (referer) {
    try {
      const refererHost = new URL(referer).host;
      if (refererHost === host) return null;
    } catch {
      // invalid referer
    }
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  // Non-browser clients (cURL, cron) don't send these — allow.
  return null;
}
