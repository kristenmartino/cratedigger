/**
 * Derive the ordered list of platform-link entries shown to readers
 * on a single record. Parity with services/agent/agent/listen_links.py
 * so the email and the web page render the same set of platforms in
 * the same order.
 *
 * Editorial preference: Bandcamp (pays artists), Spotify (popular
 * default), Apple Music (alt streaming), YouTube (broadly accessible),
 * SoundCloud (niche).
 */

const PLATFORMS = [
  { label: "Bandcamp", key: "bandcampUrl" },
  { label: "Spotify", key: "spotifyUrl" },
  { label: "Apple Music", key: "appleMusicUrl" },
  { label: "YouTube", key: "youtubeUrl" },
  { label: "SoundCloud", key: "soundcloudUrl" },
] as const;

export interface ListenLink {
  platform: string;
  url: string;
}

/**
 * Build the ordered list of {platform, url} entries. Skips platforms
 * with no URL on the record. Returns an empty array when the record
 * has no listen URLs at all — callers should omit the strip entirely
 * in that case.
 */
export function buildListenLinks(
  record: Partial<Record<(typeof PLATFORMS)[number]["key"], string | null>>,
): ListenLink[] {
  const out: ListenLink[] = [];
  for (const { label, key } of PLATFORMS) {
    const url = record[key];
    if (typeof url === "string" && url.trim()) {
      out.push({ platform: label, url });
    }
  }
  return out;
}
