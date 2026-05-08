# Crate Digger — Design System

Tokens extracted from the v4 mockup. The mockups are the source of truth — when in doubt, open them and inspect.

## Color palette

```css
--paper:        #EFE4CC;  /* primary background, all surfaces */
--paper-deep:   #E2D4B5;  /* subtle layering, withheld panels */
--paper-light:  #F6EED9;  /* cards, signal-blocks, matched-signals */

--ink:          #15191D;  /* primary text, borders, dark elements */
--ink-soft:     #2D353A;  /* secondary text */
--ink-faint:    #6E7378;  /* tertiary text, captions */

--teal:         #1E4543;  /* primary secondary accent */
--teal-bright:  #2C6764;  /* lighter teal, less common */
--teal-deep:    #16302E;  /* title bars, status bars, agent contexts */

--coral:        #C8412B;  /* primary accent — used sparingly */
--coral-deep:   #93311F;  /* italic emphasis, lead-pick category */

--mustard:      #D29826;  /* highlight only — stat numbers, "Now" markers, lead-pick borders */

--rule:         rgba(21, 25, 29, 0.16);  /* dividers */
--rule-soft:    rgba(21, 25, 29, 0.08);
```

Discipline rules:
- **Coral is the main accent.** Use for primary calls-to-action, active state, "Now" elements, italic emphasis in body text.
- **Teal is the secondary accent.** Use for source tags, "matched signals" boost, archive-mode chrome.
- **Mustard is for highlights only.** Stat numbers, the lead-pick badge, the live-indicator dot. Never for body text.
- **No additional colors.** If a feature seems to need a new color, the design system needs adjusting first.

## Typography

```css
--font-display: "Instrument Serif", serif;       /* italic only — wordmark, titles, pull quotes, signature numerals */
--font-body:    "Spectral", Georgia, serif;      /* all body prose */
--font-mono:    "DM Mono", monospace;            /* metadata, source tags, system-strip, captions */
--font-script:  "Caveat", cursive;               /* used only twice: editor's signature + signoff */
```

### Type scale (editorial)

```
wordmark            128px italic Instrument Serif (responsive: clamp 80→128)
issue display       168px italic Instrument Serif (with hand-drawn coral underline)
section header h2    56px italic Instrument Serif
lead title           76px italic Instrument Serif
inside title         46px italic Instrument Serif
withheld title       40px italic Instrument Serif
pullquote            32px italic Instrument Serif

lead dek             21px italic Spectral 300
editor's note        19px italic Spectral 300 (with 86px italic Instrument Serif drop cap)
lead prose           17px Spectral 300 (line-height 1.72)
inside prose         16.5px Spectral 300 (line-height 1.7)

source tag           9.5px DM Mono uppercase, 0.18em letter-spacing
signal badge         9.5px DM Mono uppercase, 0.22em letter-spacing
archive-link         10px DM Mono uppercase, 0.2em letter-spacing

editor signature     32px Caveat (italic-feel)
signoff              38px Caveat
```

### Type scale (archive)

```
window title         11px DM Mono uppercase, 0.2em
tab labels           10px DM Mono uppercase, 0.2em
issue header num     84px italic Instrument Serif
issue header title   32px italic Instrument Serif
record title         20px italic Instrument Serif
filter labels        11px DM Mono
state badges         8.5px DM Mono uppercase, 0.18em
status bar           9.5px DM Mono uppercase, 0.18em
```

### Microtypography

- Always use smart quotes: `'` `"` not `'` `"`
- Em dashes are real em dashes: `—` not `--`
- Old-style figures: `font-feature-settings: "onum"` on body
- Tabular figures: `font-feature-settings: "tnum"` on stats and dates
- Ligatures enabled: `font-feature-settings: "kern", "liga"`

## Spacing

The mockup uses a loose 8px grid but isn't strict about it. Common values:

```
4, 6, 8, 10, 12, 14, 16, 18, 22, 24, 28, 32, 36, 40, 50, 60, 80, 110, 130 px
```

Page padding:
- Desktop editorial: `0 60px 100px`
- Desktop archive: contained in window, `28px 40px 32px` canvas
- Mobile: `0 22px 80px` (or `0 18px 70px` on narrow phones)

Vertical rhythm: sections separated by 60–110px. Cards padded 28–40px. Inline elements 12–18px.

## Borders and shadows

- Hairline rules: `1px solid var(--rule-soft)` or `0.5px solid var(--rule)`
- Container borders: `1px solid var(--ink)` or `1.5px solid var(--ink)` for emphasis
- Card shadows (rare, used on covers and detail panels):
  ```css
  box-shadow:
    0 1px 0 rgba(21, 25, 29, 0.05),
    0 24px 60px -16px rgba(21, 25, 29, 0.18);
  ```
- Window shadow (archive only):
  ```css
  box-shadow:
    0 1px 0 rgba(21, 25, 29, 0.08),
    0 28px 60px -16px rgba(21, 25, 29, 0.22),
    0 12px 28px -8px rgba(21, 25, 29, 0.12);
  ```

## Texture

Both surfaces use a subtle paper grain via SVG noise filter:

```html
<svg xmlns='http://www.w3.org/2000/svg' width='320' height='320'>
  <filter id='n'>
    <feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/>
    <feColorMatrix values='0 0 0 0 0.55 0 0 0 0 0.45 0 0 0 0 0.25 0 0 0 0.07 0'/>
  </filter>
  <rect width='100%' height='100%' filter='url(#n)'/>
</svg>
```

Apply via `body::before` with `mix-blend-mode: multiply` and `opacity: 0.45`.

Cover art uses a similar halftone-dot pattern overlaid for riso-print authenticity:

```svg
<pattern id="halftone" x="0" y="0" width="5" height="5" patternUnits="userSpaceOnUse">
  <circle cx="2.5" cy="2.5" r="0.55" fill="#15191D" opacity="0.06"/>
</pattern>
```

## Motion

Mostly static. Two motion patterns are real:

1. **Pulse** (the agent-status dot, the now-digging dot):
   ```css
   @keyframes pulse {
     0%, 100% { box-shadow: 0 0 0 0 rgba(200, 65, 43, 0.6); }
     50%      { box-shadow: 0 0 0 6px rgba(200, 65, 43, 0); }
   }
   /* 2.4s ease-in-out infinite */
   ```

2. **Hover lifts** on archive elements (records, dividers): `transform: translateY(-4px to -6px)` over `0.18s ease`.

No scroll-triggered animations. No parallax. No fade-ins on scroll. The publication is static; the system is alive.

## Component map

### Editorial digest (`cratedigger-newsletter.html`)

- `system-strip` — top black bar, agent + week stats + archive link
- `masthead` — three-column meta + wordmark
- `issue-open` — eyebrow + giant 04 + issue date
- `editors-note` — drop cap + italic + Caveat signature
- `sec-header` — numbered section dividers
- `lead` — full editorial spread (cover + metadata sidebar + text col)
- `signal-block` — lead/steady/stretch category indicator (NEW v4)
- `pullquote` — italic centered with thin coral rules
- `matched-signals` — "Why this matched you" tag block (NEW v4)
- `actions` — Listen + feedback buttons + archive link
- `inside-track` — alternating L/R compact track layout
- `withheld` — wax seal + copy + CTA
- `track-nav` — sticky right-side scroll nav (NEW v4, desktop only)
- `next-issue` — black panel teaser
- `colophon` — three-column credits + loop stats
- `now-digging` — ambient agent-status widget (NEW v4)
- `signoff` — Caveat handwriting

### Archive (`cratedigger-archive.html`)

- `window` — outer chrome with title bar, menu bar, body, status bar
- `title-bar` — traffic lights + title + actions
- `menu-bar` — tabs (Issues/Records/Sources/Stats) + agent-status
- `sidebar` — four blocks: Filter, Issues, Sources, Tags
- `crate-strip` — issue dividers row
- `divider.active` — selected issue (drops down with arrow)
- `divider.future` — barber-pole hatched, non-clickable
- `issue-header` — large 04 + meta + actions
- `record` — sleeve card with state badge
- `state-badge` — Lead/Hit/Miss/Withheld categorization
- `detail-strip` — last-selected indicator with mini-cover
- `status-bar` — aggregate stats + brand mark + keyboard hints

## Mobile responsiveness

Editorial is mobile-first: every component stacks gracefully at <768px.

Archive degrades on mobile but isn't optimized — the window-chrome metaphor doesn't translate, so the sidebar collapses to a horizontally-scrolling row of filter blocks, the records grid drops to 2 columns, and the status bar stacks vertically. Functional, not beautiful. v2 may rebuild a mobile-native archive view.

## What NOT to do

- Don't add icons unless absolutely necessary. The design uses Unicode marks (▸ ◆ ★ ⌁ ❦) and SVG-drawn elements. No icon library.
- Don't pull in a UI component library (no Radix, no shadcn, no Material). The design is bespoke.
- Don't introduce gradients. The two existing gradients (the dreamy MFM cover, the next-issue radial glow) are the exceptions, not the pattern.
- Don't add box-shadows beyond the two defined above.
- Don't use border-radius. The design is rectilinear except for the wax-seal circle.
- Don't add emoji. The Caveat heart `♥` in the signoff is the only one.
