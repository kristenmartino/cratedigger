/**
 * In-memory sliding-window rate limiter. Harvested verbatim from
 * sift/lib/rate-limit.ts. Suitable for single-instance Vercel serverless
 * (resets per cold start). For horizontal scale, replace with Redis.
 */
interface RateLimitEntry {
  timestamps: number[];
}

const store = new Map<string, RateLimitEntry>();

const CLEANUP_INTERVAL_MS = 60_000;
let lastCleanup = Date.now();

function cleanup(windowMs: number) {
  const now = Date.now();
  if (now - lastCleanup < CLEANUP_INTERVAL_MS) return;
  lastCleanup = now;
  const cutoff = now - windowMs;
  for (const [key, entry] of store) {
    entry.timestamps = entry.timestamps.filter((t) => t > cutoff);
    if (entry.timestamps.length === 0) store.delete(key);
  }
}

export function rateLimit(
  key: string,
  { maxRequests, windowMs }: { maxRequests: number; windowMs: number },
): { allowed: boolean; remaining: number; retryAfterMs: number } {
  cleanup(windowMs);

  const now = Date.now();
  const cutoff = now - windowMs;
  const entry = store.get(key) || { timestamps: [] };

  entry.timestamps = entry.timestamps.filter((t) => t > cutoff);

  if (entry.timestamps.length >= maxRequests) {
    const oldest = entry.timestamps[0];
    return {
      allowed: false,
      remaining: 0,
      retryAfterMs: oldest + windowMs - now,
    };
  }

  entry.timestamps.push(now);
  store.set(key, entry);

  return {
    allowed: true,
    remaining: maxRequests - entry.timestamps.length,
    retryAfterMs: 0,
  };
}
