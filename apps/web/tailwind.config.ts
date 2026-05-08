import type { Config } from "tailwindcss";

/**
 * Crate Digger design tokens. Sourced from cratedigger-handoff/DESIGN_SYSTEM.md.
 * The mockups (cratedigger-newsletter.html, cratedigger-archive.html) are the
 * pixel-level source of truth; this config encodes their tokens for component
 * use.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: {
          DEFAULT: "#EFE4CC",
          deep: "#E2D4B5",
          light: "#F6EED9",
        },
        ink: {
          DEFAULT: "#15191D",
          soft: "#2D353A",
          faint: "#6E7378",
          quiet: "#969799",
        },
        teal: {
          DEFAULT: "#1E4543",
          bright: "#2C6764",
          deep: "#16302E",
        },
        coral: {
          DEFAULT: "#C8412B",
          deep: "#93311F",
        },
        mustard: "#D29826",
        rule: {
          DEFAULT: "rgba(21, 25, 29, 0.16)",
          soft: "rgba(21, 25, 29, 0.08)",
        },
      },
      fontFamily: {
        display: ['"Instrument Serif"', "serif"],
        body: ['"Spectral"', "Georgia", "serif"],
        mono: ['"DM Mono"', "monospace"],
        script: ['"Caveat"', "cursive"],
      },
      fontSize: {
        // Editorial type scale (DESIGN_SYSTEM.md "Type scale (editorial)")
        wordmark: ["clamp(64px, 9vw, 104px)", { lineHeight: "0.85", letterSpacing: "-0.025em" }],
        "issue-display": ["168px", { lineHeight: "0.78", letterSpacing: "-0.045em" }],
        "section-h": ["56px", { lineHeight: "0.92", letterSpacing: "-0.025em" }],
        "lead-title": ["76px", { lineHeight: "0.92", letterSpacing: "-0.035em" }],
        "inside-title": ["46px", { lineHeight: "0.95", letterSpacing: "-0.025em" }],
        "withheld-title": ["40px", { lineHeight: "1", letterSpacing: "-0.025em" }],
        pullquote: ["32px", { lineHeight: "1.2", letterSpacing: "-0.015em" }],
        "lead-dek": ["21px", { lineHeight: "1.45" }],
        "editor-note": ["19px", { lineHeight: "1.65" }],
        "lead-prose": ["17px", { lineHeight: "1.72" }],
        "inside-prose": ["16.5px", { lineHeight: "1.7" }],
        "source-tag": ["9.5px", { letterSpacing: "0.18em" }],
        "signal-badge": ["9.5px", { letterSpacing: "0.22em" }],
        "archive-link": ["10px", { letterSpacing: "0.2em" }],
        signature: ["32px", { lineHeight: "1" }],
        signoff: ["38px", { lineHeight: "1", letterSpacing: "0.01em" }],
      },
      keyframes: {
        // Pulse for the agent-status / now-digging dots
        "agent-pulse": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(200, 65, 43, 0.6)" },
          "50%": { boxShadow: "0 0 0 6px rgba(200, 65, 43, 0)" },
        },
      },
      animation: {
        "agent-pulse": "agent-pulse 2.4s ease-in-out infinite",
      },
      boxShadow: {
        // From DESIGN_SYSTEM.md
        cover: "0 1px 0 rgba(21, 25, 29, 0.05), 0 24px 60px -16px rgba(21, 25, 29, 0.18)",
        window:
          "0 1px 0 rgba(21, 25, 29, 0.08), 0 28px 60px -16px rgba(21, 25, 29, 0.22), 0 12px 28px -8px rgba(21, 25, 29, 0.12)",
      },
    },
  },
  plugins: [],
};

export default config;
