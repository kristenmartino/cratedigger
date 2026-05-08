/**
 * Generic helpers. Harvested from sift/lib/utils.ts and trimmed for CD's
 * domain (dropped formatUsdCompact, estimateReadTime).
 */

/**
 * Format a date string as relative time. "5m ago", "2h ago", "3d ago".
 * Used by the system-strip and now-digging widgets.
 */
export function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "Recently";
  const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (isNaN(diff) || diff < 0) return "Recently";
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/**
 * Stable djb2-style hash. Used for client-side keys when a real UUID
 * isn't yet present (e.g. optimistic UI before server returns).
 */
export function stableHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
  }
  return Math.abs(hash).toString(36);
}

/**
 * "https://www.boomkat.com/path" → "Boomkat".
 */
export function extractSourceDomain(url: string): string {
  try {
    const hostname = new URL(url).hostname.replace("www.", "");
    const name = hostname.split(".")[0];
    return name.charAt(0).toUpperCase() + name.slice(1);
  } catch {
    return "Source";
  }
}
