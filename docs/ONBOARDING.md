# Onboarding

How new users get added to Crate Digger and start receiving Sunday emails.

**Tier 1 is now live** — Clerk webhook auto-creates the Neon row, and a
`/onboarding` form captures their taste seed. The legacy manual flow
(Tier 0) remains as an escape hatch and is documented at the bottom.

## Tier 1 flow (~30 sec for the invitee, zero admin steps)

### What happens

1. Invitee visits the site, clicks "Sign in," completes Clerk signup
2. Clerk fires `user.created` → our webhook (`/api/clerk/webhook`)
   creates the `users` row in Neon
3. The home page sees they have no `taste_profiles` row → redirects
   them to `/onboarding`
4. They paste 10–50 artists (one per line) + pick 3–12 genre chips,
   click "Set my taste"
5. `/api/onboarding` persists the seed to `taste_profiles.seed` and
   fires a fire-and-forget POST to the agent's `/v1/build-taste-profile`
6. Agent embeds the seed via Voyage, writes `taste_centroid` + tag weights
7. Next Sunday cron enumerates users-with-profiles and includes them

### Required env vars

| Var | Where | Notes |
|---|---|---|
| `CLERK_WEBHOOK_SECRET` | Vercel | From Clerk dashboard → Webhooks → endpoint signing secret |
| `PIPELINE_API_KEY` | Vercel + Railway | Same value in both — gates the agent's `/v1/build-taste-profile` and `/v1/run-issue` |
| `AGENT_URL` | Vercel | e.g. `https://cratedigger-agent.up.railway.app` |
| `VOYAGE_API_KEY` | Railway | Needed for the centroid embedding step |

### Clerk dashboard setup (one-time)

1. Clerk dashboard → **Webhooks** → **Add Endpoint**
2. URL: `https://cratedigger.kristenmartino.ai/api/clerk/webhook`
3. Subscribe to: `user.created`, `user.updated`, `user.deleted`
4. Copy the **Signing Secret** → set as `CLERK_WEBHOOK_SECRET` in Vercel env
5. Clerk's Svix-backed delivery retries with backoff on non-2xx, so a
   brief Vercel outage won't lose a signup

### Spotify playlist shortcut (Tier 2)

The form has an optional "Have a playlist? Paste it." field. Pasting a
public Spotify playlist URL → `/api/onboarding/parse-playlist` proxies to
the agent's `POST /v1/parse-playlist`, which uses our Client Credentials
token to read the playlist's tracks and pre-fills the artists textarea
with up to 50 unique artists (preserving playlist order — head-of-list
artists usually encode taste priority).

Works for any public playlist; private playlists return empty (the user
falls back to manual entry). Requires `SPOTIFY_CLIENT_ID` and
`SPOTIFY_CLIENT_SECRET` set on Railway — same credentials we already use
for catalog search in the metadata enrichment pipeline.

Supported URL shapes: standard `open.spotify.com/playlist/<id>`,
intl-redirected `open.spotify.com/intl-en/playlist/<id>`, the
`?si=…` share-link variant, the desktop-client `spotify:playlist:<id>`
URI, and bare 22-char IDs.

### What the seed looks like

The form persists this JSONB into `taste_profiles.seed`:

```json
{
  "version": 1,
  "kind": "manual",
  "artists": ["Burial", "Four Tet", "Built to Spill", "..."],
  "tags": ["ambient", "indie", "dub"]
}
```

Same shape as the legacy `services/agent/data/seeds/<clerk_id>.json` files —
which means the existing `build_profile_from_seed` and
`upsert_taste_profile` paths just work.

### Editing a seed later

No UI yet. For now, either:

1. Have the user re-run `/onboarding` — the API does
   `ON CONFLICT (user_id) DO UPDATE`, so a re-submit replaces the seed
   and triggers a re-embed
2. Or `psql` directly:
   ```sql
   UPDATE taste_profiles
      SET seed = '{"version":1,"kind":"manual","artists":[...],"tags":[...]}'::jsonb
    WHERE user_id = (SELECT id FROM users WHERE clerk_id = 'user_2dRkF...');
   ```
   then call `POST /v1/build-taste-profile` with `X-Pipeline-Key`

## Cost per invitee

- **Voyage** embedding: one call per submit × $0.00012/call ≈ **$0.0001**
- **Anthropic** (per Sunday issue): ~$0.03/issue
- **Resend** email send: free tier covers this

Cents per invitee per week. Scaling to dozens is fine.

## Tier 0 — manual escape hatch (legacy)

If the webhook is down, the form is broken, or you're scripting bulk
imports, the original two-step flow still works:

```bash
# 1. Create the users row
DATABASE_URL=postgresql://... \
  python services/agent/scripts/add_user.py \
    --clerk-id user_2dRkF... \
    --email alice@example.com

# 2. Drop their seed file at services/agent/data/seeds/<clerk_id>.json
#    (same shape as user_kristen_seed_v1.json) and push.
#    The rebuild-taste-profile workflow auto-fires on changes under
#    data/seeds/** and rebuilds every user's profile.
```

This path is preserved for ops convenience but is not the default. New
invitees should always be encouraged toward the Tier 1 form.

## Future

### Tier 2 — richer seed inputs

- Spotify playlist URL paste (parses tracks → artists)
- Listening-history JSON upload (Last.fm / Spotify "Your Data" export)
- Live MusicBrainz autocomplete on the artist field

### Tier 3 — Spotify OAuth

- Per-user Spotify OAuth — both for *reading* listening history as
  taste-seed input AND *writing* weekly picks back to a rolling
  "Crate Digger" playlist that auto-updates each Sunday

## What this doesn't handle yet

- **Per-user source weights.** Today everyone shares
  `sources.default_weight`. The taste profile carries a `source_weights`
  JSONB that's set to a default — not yet customizable in onboarding.
- **Deactivation.** No "stop sending me emails" flow. Today you delete
  the `taste_profiles` row by hand; the cron only enumerates users with
  profiles.
- **Webhook backfill.** If the webhook somehow misses a signup (Vercel
  hard-down, Clerk delivery exhausts retries), the `/api/onboarding`
  handler defensively re-creates the user row from the Clerk session,
  so the form still works.
