-- =============================================================================
-- Crate Digger — Quality Dashboard
-- =============================================================================
--
-- One-paste operator visibility. Run section-by-section in Neon's SQL editor
-- (or `psql -f`) to see whether the pipeline is healthy, where it's degrading,
-- and what to fix next.
--
-- Sections, in priority order:
--   1. Pipeline run history       — did recent runs complete, where did they fail
--   2. Source ingest health       — are all 10 sources contributing, when did
--                                   each last produce
--   3. Metadata enrichment        — MB + Discogs hit rates (cover, bandcamp,
--                                   spotify) for recent releases
--   4. Editorial output           — issues + recommendations: how many slots
--                                   are being filled, what scores are clearing
--                                   the thresholds, was email delivered
--   5. Scoring distribution       — within recent picks, what does the score
--                                   distribution look like vs the SPEC
--                                   thresholds (Lead ≥ 0.85, etc.)
--
-- Re-run weekly after the Sunday cron to monitor regression.
-- =============================================================================


-- ─── 1. Pipeline run history ────────────────────────────────────────────────
-- Healthy: status='completed', current_step='done', notes empty, elapsed in
-- the 30-180s range.
-- Stalled: current_step != 'done' on a completed run = a node returned
-- without advancing. Notes column has the cause (post PR #17).
-- Failed: status='failed'; notes has the error.

SELECT
    started_at::date AS started_date,
    started_at::time(0) AS started_time,
    status,
    current_step,
    sources_scanned AS srcs,
    releases_scanned AS rel,
    candidates_considered AS cand,
    records_surfaced AS picks,
    releases_dropped_as_news AS news_dropped,
    EXTRACT(EPOCH FROM (COALESCE(completed_at, NOW()) - started_at))::int AS elapsed_s,
    LEFT(notes, 120) AS notes
FROM agent_runs
ORDER BY started_at DESC
LIMIT 10;


-- ─── 2. Source ingest health ────────────────────────────────────────────────
-- Active row count: should match data/sources.json's active set (currently
-- 10). Anything inactive should have an explanation in docs/SOURCES.md.
-- last_crawled_at: if > 8 days old on an active source, the cron is broken or
-- the source URL is dead.

SELECT
    slug,
    ingest_method,
    active,
    last_crawled_at::date AS last_crawled,
    AGE(NOW(), last_crawled_at) AS time_since_crawl,
    default_weight
FROM sources
ORDER BY active DESC, last_crawled_at DESC NULLS LAST;


-- ─── 3a. Metadata enrichment hit rates ──────────────────────────────────────
-- Recent releases (last 7 days). The "no wrong covers" rule from PR #22 means
-- this number is honest — anything counted in `with_cover` is a verified
-- match, not a guess.
--
-- Healthy after enrichment runs at scale:
--   cover_pct       ≥ 50%  (MB has good coverage post-2010)
--   bandcamp_pct    ≥ 20%  (only Bandcamp-linked artists)
--   spotify_pct     ≥ 30%
-- Below these → likely an MB rate-limit issue, or artist names not matching
-- after our strict-match filter.

SELECT
    COUNT(*) AS total_releases,
    COUNT(cover_art_url) AS with_cover,
    COUNT(bandcamp_url) AS with_bandcamp,
    COUNT(spotify_url) AS with_spotify,
    COUNT(apple_music_url) AS with_apple_music,
    COUNT(youtube_url) AS with_youtube,
    ROUND(100.0 * COUNT(cover_art_url) / NULLIF(COUNT(*), 0), 1) AS cover_pct,
    ROUND(100.0 * COUNT(bandcamp_url) / NULLIF(COUNT(*), 0), 1) AS bandcamp_pct,
    ROUND(100.0 * COUNT(spotify_url) / NULLIF(COUNT(*), 0), 1) AS spotify_pct,
    ROUND(100.0 * COUNT(apple_music_url) / NULLIF(COUNT(*), 0), 1) AS apple_music_pct,
    ROUND(100.0 * COUNT(youtube_url) / NULLIF(COUNT(*), 0), 1) AS youtube_pct
FROM releases
WHERE first_seen_at > NOW() - INTERVAL '7 days';


-- ─── 3b. Metadata enrichment by source ──────────────────────────────────────
-- Some sources produce richer metadata-recoverable releases than others.
-- Hardwax / Resident Advisor release IDs map cleanly to MB; magazine
-- mentions of obscure releases map poorly.

SELECT
    source,
    COUNT(*) AS releases_seen,
    COUNT(rel.cover_art_url) AS with_cover,
    COUNT(rel.bandcamp_url) AS with_bandcamp,
    ROUND(100.0 * COUNT(rel.cover_art_url) / COUNT(*), 1) AS cover_pct,
    ROUND(100.0 * COUNT(rel.bandcamp_url) / COUNT(*), 1) AS bandcamp_pct
FROM releases rel,
     unnest(rel.sources_seen) AS source
WHERE rel.first_seen_at > NOW() - INTERVAL '14 days'
GROUP BY source
ORDER BY releases_seen DESC;


-- ─── 4a. Issues + email status ──────────────────────────────────────────────
-- Healthy: status='delivered' for every Sunday issue. status='draft' with
-- has_html=true → render worked but send didn't (Resend issue, see notes
-- via the agent_runs query above).

SELECT
    i.issue_number,
    i.publish_date,
    i.title,
    i.status,
    LENGTH(i.editor_note) AS editor_note_chars,
    i.email_html IS NOT NULL AS has_html,
    LENGTH(i.email_html) AS html_bytes,
    i.created_at::date AS created_date
FROM issues i
ORDER BY i.issue_number DESC
LIMIT 5;


-- ─── 4b. Recommendation slot coverage ───────────────────────────────────────
-- Per-issue: which of the 5 SPEC slots got filled. Sunday email shows up
-- to 4 (Lead + 2 Steady + Stretch; Withheld held for Friday). If categories
-- are missing it usually means scoring's not clearing the thresholds.
--
-- Expected pattern:
--   Lead     | Steady×2 | Stretch | Withheld   = full picks pool
-- Common degenerate pattern (today):
--   Lead via promotion | Withheld via promotion = only 2 picks, threshold-driven

SELECT
    i.issue_number,
    COUNT(r.id) AS total_recs,
    SUM(CASE WHEN r.category = 'lead'     THEN 1 ELSE 0 END) AS lead,
    SUM(CASE WHEN r.category = 'steady'   THEN 1 ELSE 0 END) AS steady,
    SUM(CASE WHEN r.category = 'stretch'  THEN 1 ELSE 0 END) AS stretch,
    SUM(CASE WHEN r.category = 'withheld' THEN 1 ELSE 0 END) AS withheld,
    ROUND(AVG(r.match_score), 3) AS avg_score,
    ROUND(MAX(r.match_score), 3) AS top_score,
    ROUND(MIN(r.match_score), 3) AS min_score
FROM issues i
LEFT JOIN recommendations r ON r.issue_id = i.id
GROUP BY i.issue_number
ORDER BY i.issue_number DESC
LIMIT 10;


-- ─── 5. Scoring vs SPEC thresholds ──────────────────────────────────────────
-- The SPEC sets Lead ≥ 0.85, Steady ≥ 0.65, Stretch ≥ 0.50. The actual scoring
-- formula tops out around 0.43-0.50 in realistic conditions. This query
-- shows how often each threshold is actually cleared — if every band is 0,
-- it's a calibration mismatch (the SPEC numbers don't match the math).

SELECT
    'Lead (≥ 0.85)'   AS bucket,
    COUNT(*) FILTER (WHERE r.match_score >= 0.85) AS picks_meeting,
    COUNT(*) AS picks_total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE r.match_score >= 0.85) / NULLIF(COUNT(*), 0), 1) AS pct
FROM recommendations r
WHERE r.created_at > NOW() - INTERVAL '60 days'

UNION ALL

SELECT
    'Steady (≥ 0.65)' AS bucket,
    COUNT(*) FILTER (WHERE r.match_score >= 0.65 AND r.match_score < 0.85) AS picks_meeting,
    COUNT(*) AS picks_total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE r.match_score >= 0.65 AND r.match_score < 0.85) / NULLIF(COUNT(*), 0), 1) AS pct
FROM recommendations r
WHERE r.created_at > NOW() - INTERVAL '60 days'

UNION ALL

SELECT
    'Stretch (≥ 0.50)' AS bucket,
    COUNT(*) FILTER (WHERE r.match_score >= 0.50 AND r.match_score < 0.65) AS picks_meeting,
    COUNT(*) AS picks_total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE r.match_score >= 0.50 AND r.match_score < 0.65) / NULLIF(COUNT(*), 0), 1) AS pct
FROM recommendations r
WHERE r.created_at > NOW() - INTERVAL '60 days'

UNION ALL

SELECT
    'Below all'       AS bucket,
    COUNT(*) FILTER (WHERE r.match_score < 0.50) AS picks_meeting,
    COUNT(*) AS picks_total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE r.match_score < 0.50) / NULLIF(COUNT(*), 0), 1) AS pct
FROM recommendations r
WHERE r.created_at > NOW() - INTERVAL '60 days';


-- ─── 6. Failed or stalled runs ──────────────────────────────────────────────
-- Anything in this list is either currently broken or was a one-time
-- failure worth understanding. After PR #17, notes contains the specific
-- reason (e.g., "send_email skipped: RESEND_API_KEY unset").

SELECT
    started_at,
    status,
    current_step,
    sources_scanned,
    releases_scanned,
    LEFT(notes, 200) AS notes
FROM agent_runs
WHERE status = 'failed'
   OR (status = 'completed' AND current_step != 'done')
ORDER BY started_at DESC
LIMIT 10;
