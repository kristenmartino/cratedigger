import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://cratedigger.kristenmartino.ai"),
  title: {
    default: "Crate Digger",
    template: "%s — Crate Digger",
  },
  description:
    "A weekly AI-curated music recommendation digest. Four records every Sunday morning, one held back for Friday.",
  openGraph: {
    type: "website",
    siteName: "Crate Digger",
    title: "Crate Digger",
    description: "A weekly dispatch of music worth digging for.",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClerkProvider>
      <html lang="en" suppressHydrationWarning>
        <head>
          <link rel="preconnect" href="https://fonts.googleapis.com" />
          <link
            rel="preconnect"
            href="https://fonts.gstatic.com"
            crossOrigin="anonymous"
          />
          <link
            rel="stylesheet"
            href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Spectral:ital,wght@0,300;0,400;0,500;0,600;1,300;1,400;1,500&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Caveat:wght@400;500&display=swap"
          />
        </head>
        <body>
          <a href="#main-content" className="sr-only focus:not-sr-only">
            Skip to content
          </a>
          <main id="main-content">{children}</main>
        </body>
      </html>
    </ClerkProvider>
  );
}
