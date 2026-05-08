# @cratedigger/email

MJML email template + Resend wrapper. Renders the same `Issue` shape as `apps/web`'s editorial digest.

## Render

```ts
import { renderIssue } from "@cratedigger/email";
const { html, errors } = renderIssue(issue);
```

## Conventions

- Email and web pull from the same `Issue` shape (`@cratedigger/shared`). Don't write the editorial twice — see CLAUDE.md "Conventions".
- Email simplifies vs web: no sticky nav, halftone covers as inline base64 PNG (Outlook doesn't do SVG well), "Open in browser" link at top, "Open in archive" at bottom.
- Test deliverability via Litmus or Mailtrap before any production send.

## Status

Stub. Full editorial layout lands Sprint Week 6 per `cratedigger-handoff/SPRINT_PLAN.md`.
