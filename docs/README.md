# Crate Digger — Project Handoff

A weekly AI-curated music recommendation digest. Two surfaces (an editorial publication that lands Sunday mornings and an archive tool for digging through past issues), one agentic backend that reads music critics and scores releases against a personal taste model.

This package is a handoff to **Claude Code** (Anthropic's CLI coding tool). It contains everything Claude Code needs to start building v1, plus the mockups that lock in design intent.

## What's in this folder

```
docs/                      ← (originally `cratedigger-handoff/`; moved in-tree)
├── README.md              ← you are here
├── CLAUDE.md              ← persistent context for Claude Code sessions
├── SPEC.md                ← technical architecture, data model, agent flows
├── SPRINT_PLAN.md         ← 6-week v1 build plan + phased v1.1, v1.2, v1.3
├── DESIGN_SYSTEM.md       ← design tokens extracted from mockups
└── mockups/
    ├── cratedigger-newsletter.html       ← editorial digest (primary newsletter surface) — v4
    ├── cratedigger-archive.html    ← archive tool (secondary "digging" surface)
    └── cratedigger-editorial-alt.html  ← alternate editorial direction (kept for reference)
```

## How to use this with Claude Code

1. Unzip into a project directory: `~/projects/cratedigger/`
2. Open the directory in your terminal
3. Run `claude` to start a session
4. Claude Code will read `CLAUDE.md` automatically and know the project context
5. First-session prompts are at the bottom of `CLAUDE.md` — copy one to start

## What you're getting from this handoff vs. starting from scratch

The mockups are not throwaway design exploration — they're tokenized, the type system is locked, the SVG cover art is reusable, and the v4 editorial mockup includes the actual copy structure and signal-block / matched-signals patterns the agent needs to populate. Claude Code's job is mostly assembly, not greenfield design.

The spec assumes ~60% reuse of the Sift codebase (Next.js 15 + FastAPI + LangGraph + Neon pgvector + Voyage AI + Claude Haiku 4.5 + Vercel + Railway + Clerk). If you want to fork from Sift rather than start clean, that's the recommended path — most of the agent infrastructure already exists.

## Project identity

- **Working name:** Crate Digger
- **Domain:** `cratedigger.kristenmartino.ai` (originally planned as independent `cratedigger.ai`; reversed 2026-05-08 — see `SPRINT_PLAN.md` decision log)
- **Cadence:** Weekly Sunday delivery, Friday surprise drop for subscribers
- **Format:** Email primary, web archive secondary, both rendered from same content
- **Audience:** v1 is for Kristen herself; v1.3 opens to a small invite list

## Portfolio framing

This project demonstrates: agentic system design (multi-source ingestion + scoring + reasoning + feedback), AI-product UX (calibrated confidence, legible model signals, two-surface architecture), and the designer-PM identity (real visual craft + real systems thinking in one project). Target roles: Deloitte Agentic Delivery, NextEra AI PM, Anthropic Applied AI, and adjacent.

Built with Claude Code. Designed to be a short, honest answer to "show me a project."
