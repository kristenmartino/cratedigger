/** @type {import('next').NextConfig} */
const nextConfig = {
  // Top-level in Next 15.5+ (was experimental.typedRoutes before).
  typedRoutes: true,
  images: {
    // Cover art comes from Bandcamp, Discogs, MusicBrainz, label sites — diverse CDNs.
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
  async headers() {
    const csp = [
      "default-src 'self'",
      // 'unsafe-inline' for Tailwind injected styles + Clerk UI + theme init script
      "script-src 'self' 'unsafe-inline' https://*.clerk.accounts.dev https://*.clerk.services https://clerk.cratedigger.kristenmartino.ai https://challenges.cloudflare.com",
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' https://fonts.gstatic.com data:",
      "img-src 'self' https: data:",
      "connect-src 'self' https://*.clerk.accounts.dev https://*.clerk.services https://clerk.cratedigger.kristenmartino.ai http://localhost:8000 https://api.cratedigger.kristenmartino.ai",
      "frame-src https://*.clerk.accounts.dev https://*.clerk.services https://clerk.cratedigger.kristenmartino.ai https://challenges.cloudflare.com",
      "frame-ancestors 'none'",
      "form-action 'self'",
      "base-uri 'self'",
      "worker-src 'self' blob:",
    ].join("; ");

    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: csp },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
