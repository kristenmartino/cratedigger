# @cratedigger/shared

Shared TypeScript types backed by zod runtime schemas. Used by `apps/web` (validates API responses) and mirrored by hand in `services/api/app/models.py` (Pydantic).

## Sync rule

When you change a type here, update the matching Pydantic model in `services/api/app/models.py`. There's no codegen — discipline-driven sync (see `docs/CLAUDE.md` "Conventions").
