/**
 * Sanitize untrusted text content (release titles, label descriptions, source
 * names) by stripping all HTML tags. Prevents stored XSS from RSS feeds and
 * scraped pages.
 *
 * Harvested verbatim from sift/lib/sanitize.ts.
 */
function decodeEntities(text: string): string {
  return text
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&#x2F;/g, "/")
    .replace(/&#(\d+);/g, (_, dec) => String.fromCharCode(Number(dec)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

export function stripHtml(text: string): string {
  const decoded = decodeEntities(text);
  return decoded.replace(/<[^>]*>/g, "").trim();
}

export function sanitizeUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" && url.protocol !== "http:") {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}
