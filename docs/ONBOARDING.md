# Onboarding

How new users get added to Crate Digger and start receiving Sunday emails.

This is **Tier 0** — manual admin invitation. Tier 1 (Clerk webhook + admin
form) and Tier 2 (user-facing onboarding UI) replace pieces of this flow as
they ship.

## Tier 0 flow (~3 min per invitee)

### 1. Invitee signs up

Send them to https://cratedigger.kristenmartino.ai and have them click
"Sign in." Clerk handles the email/password (or SSO) flow.

After they finish, they exist in **Clerk** but not yet in **Neon**. Two
sources of truth that the eventual Tier 1 webhook will sync.

### 2. Grab their Clerk ID

In the Clerk dashboard → Users → click them → copy the **User ID**
(format: `user_2dRkF...`).

### 3. Create their Neon `users` row

```bash
DATABASE_URL=postgresql://... \
  python services/agent/scripts/add_user.py \
    --clerk-id user_2dRkF... \
    --email alice@example.com
```

Output prints their new UUID. Save it.

### 4. Get their taste seed

Easiest path: ask them for "10–20 artists you've been into recently" and "6–10
micro-genres you want surfaced." Same format as
[`services/agent/data/seeds/user_kristen_seed_v1.json`](../services/agent/data/seeds/user_kristen_seed_v1.json).

Future Tier 2 will replace this with a quiz UI or Spotify playlist paste.
For now, DM/email.

### 5. Commit their seed file

Create `services/agent/data/seeds/<clerk_id>.json` with the same shape as
the Kristen file:

```json
{
  "version": 1,
  "kind": "manual",
  "artists": [ "..." ],
  "tags":    [ "..." ]
}
```

Commit + push. **The `rebuild-taste-profile` workflow auto-fires** on any
change under `data/seeds/**` and rebuilds every user's profile.

If you want to rebuild just one user (e.g. after a tag tweak that doesn't
warrant re-running everyone's Voyage embeddings):

> Actions → **rebuild taste profile** → Run workflow → enter `clerk_id` →
> Run

Leave the `clerk_id` input blank to rebuild all users (same as the
path-trigger behavior).

### 6. Verify

The workflow's step summary should say `Rebuilt taste profile for
<clerk_id>`. The next Sunday cron at 02:00 UTC will enumerate users with
profiles and include them — they start receiving issues that Sunday morning.

To verify immediately without waiting:

```bash
psql "$DATABASE_URL" -c "
  SELECT u.email, tp.updated_at,
         jsonb_array_length(tp.tags) -- or however many keys
    FROM users u
    JOIN taste_profiles tp ON tp.user_id = u.id
   WHERE u.clerk_id = 'user_2dRkF...';"
```

## What changes when this gets automated

### Tier 1 (planned, ~2-3 hrs to ship)

- **Clerk webhook** on `user.created` → automatic Neon row creation. Step
  3 of Tier 0 becomes a no-op.
- **`/admin/onboard`** page in the Next.js app: dropdown of users without
  profiles + textareas for their seed input. Posts to a new Railway
  endpoint that runs `build_profile_from_seed`. Replaces step 5's
  manual-PR loop.

### Tier 2 (real onboarding, ~1-2 days)

- **`/onboarding`** page that invitees see themselves on first login.
  Three input modes: manual artist picker (MusicBrainz autocomplete),
  Spotify playlist URL paste, listening-history JSON upload.
- Bypasses the entire admin loop. They self-serve.

### Tier 3 (public): Spotify integration

- Per-user Spotify OAuth — both for *reading* their listening history as
  taste-seed input AND *writing* their weekly picks back to a rolling
  "Crate Digger" playlist that auto-updates each Sunday.
- See the conversation log for the full architectural sketch.

## Cost per invitee

- **Voyage** embedding: one call per seed artist (~20–30) × $0.00012/call
  ≈ **$0.003** per profile rebuild
- **Anthropic** (per Sunday email): identical to Kristen's, ~$0.03/issue
- **Resend** email send: $0 on free tier, $0.0004/send beyond

Total: cents per invitee per week. Scaling to dozens is fine; hundreds
needs a Resend / Voyage paid tier.

## What this doesn't handle

- **Per-user source weights.** Right now everyone shares the same
  `sources.default_weight`. The taste profile carries a `source_weights`
  dict, but it's not yet populated from seed input. Future work.
- **Deactivation.** No flow yet for "stop sending me emails." Today you
  delete the `taste_profiles` row by hand — the cron only enumerates
  users with profiles.
- **Email-address changes** after onboarding. Re-run `add_user.py` with
  the same `clerk_id` and the new email — the `ON CONFLICT DO UPDATE`
  handles it.
