"""Weekly issue pipeline (LangGraph).

Skeleton harvested from sift-api/workflows/pipeline_workflow.py. The
StateGraph(TypedDict) shape and node-as-async-function pattern transfer
verbatim. Nodes are rewritten 1:1 for Crate Digger's pipeline per SPEC.md §4:

    ingest_sources → normalize_releases → embed_releases → score_for_user
      → categorize_picks → generate_prose (parallel per record)
      → generate_pull_quote (lead only) → generate_editor_note
      → persist_issue → render_email → send_email

Each node returns a partial state update that LangGraph merges. The agent_runs
table is written incrementally so the "now digging" widget can show progress
in real time.

Email render/send remain no-op stubs in this happy-path build (Sprint Week 6).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, TypedDict

# Note: importing langgraph triggers a `LangChainPendingDeprecationWarning`
# about `allowed_objects` defaulting changing. We don't construct the
# affected JsonPlusSerializer directly (langgraph does, internally), and
# the warning bypasses standard `warnings.filterwarnings` / `catch_warnings`
# suppression — likely emitted via a custom langchain mechanism. Living
# with the noise rather than monkey-patching warnings.warn.
from langgraph.graph import END, StateGraph

from agent.categorization import CategorizedPick, ScoredCandidate, categorize
from agent.db import get_pool
from agent.editor_note import generate_editor_note_live
from agent.embedder import embed_texts
from agent.prose import generate_prose_live, generate_pull_quote_live
from agent.runs import (
    ensure_run,
    increment_counters,
    mark_completed,
    update_agent_run,
)
from agent.scoring import derive_matched_signals, score_release
from agent.sources.boomkat import scrape_boomkat
from agent.sources.resident_advisor import scrape_resident_advisor
from agent.sources.rss import RawRelease, fetch_all_rss_sources, normalize

logger = logging.getLogger("cratedigger-agent.workflows.issue")

# Cap how many candidates flow into the LLM-cost-bearing stages. Plenty of
# headroom for a clean Lead + Steady×2 + Stretch + Withheld even after
# fatigue filtering.
MAX_CANDIDATES = 200


class IssueState(TypedDict, total=False):
    """LangGraph state for one weekly issue run."""
    user_id: str
    agent_run_id: str
    force: bool
    # Ingestion
    sources: list[dict]
    raw_releases: list[dict]
    new_releases: list[dict]
    embeddings: dict[str, list[float]]
    # Scoring + categorization
    taste_profile: dict[str, Any]
    recent_artists: list[str]
    scored: list[dict]
    picks: list[dict]
    # Reasoning
    prose: dict[str, dict]
    matched_signals: dict[str, list[dict]]
    editor_note: str
    issue_title: str
    # Persistence
    issue_id: str
    # Diagnostics
    errors: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────

def upcoming_sunday(today: date | None = None) -> date:
    """The Sunday on which the next issue ships.

    Per SPRINT_PLAN.md: issues publish Sunday morning; the Saturday-night cron
    fires at 02:00 UTC Sunday. So the publish date is always the Sunday on or
    after `today`. If `today` is already Sunday, return it.
    """
    today = today or datetime.now(timezone.utc).date()
    # Python: Monday=0 ... Sunday=6
    days_until_sunday = (6 - today.weekday()) % 7
    return today + timedelta(days=days_until_sunday)


def friday_drop_at(publish_date: date) -> datetime:
    """Timestamp at which the withheld record becomes visible.

    Friday 13:00 UTC = Friday 09:00 EDT / 08:00 EST — matches the Friday-8am-ET
    cron in SPRINT_PLAN.md and the seed fixture (`2026-05-15T13:00:00Z`).
    Five days after a Sunday publish lands on Friday.
    """
    return datetime.combine(
        publish_date + timedelta(days=5), time(13, 0), tzinfo=timezone.utc
    )


def _confidence_for(score: float) -> str:
    if score > 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def _release_tags(metadata: dict, source_genre_affinity_by_slug: dict[str, list[str]],
                  sources_seen: list[str]) -> list[str]:
    """Best-effort tag list for a release. Prefer per-release tags from
    metadata; fall back to the union of genre_affinity across the sources
    that flagged it."""
    tags = metadata.get("tags") if isinstance(metadata, dict) else None
    if isinstance(tags, list) and tags:
        return [str(t) for t in tags]
    out: list[str] = []
    seen: set[str] = set()
    for slug in sources_seen or []:
        for t in source_genre_affinity_by_slug.get(slug, []):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


# ── Nodes ────────────────────────────────────────────────────────────────

async def ingest_sources_node(state: IssueState) -> dict:
    """Fetch from all active sources (RSS + scrape). Writes agent_runs row
    with current_step='ingesting' and current_source per crawler step."""
    run_id = state["agent_run_id"]
    await ensure_run(run_id)
    await update_agent_run(run_id, current_step="ingesting", current_source=None)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, name, slug, ingest_method::text AS ingest_method,
                   ingest_url, default_weight::float8 AS default_weight,
                   genre_affinity, active
              FROM sources
             WHERE active = TRUE
            """
        )
    sources = [dict(r) for r in rows]
    logger.info("ingest_sources: %d active sources", len(sources))

    raw: list[RawRelease] = []

    # RSS sources fan out in parallel inside fetch_all_rss_sources.
    rss_only = [s for s in sources if s["ingest_method"] == "rss"]
    if rss_only:
        await update_agent_run(run_id, current_source="rss")
        rss_releases = await fetch_all_rss_sources(rss_only)
        raw.extend(rss_releases)
        await increment_counters(run_id, sources_scanned=len(rss_only))

    # Scrapers are per-source.
    for s in sources:
        if s["ingest_method"] != "scrape":
            continue
        await update_agent_run(run_id, current_source=s["slug"])
        try:
            if s["slug"] == "boomkat":
                raw.extend(await scrape_boomkat())
            elif s["slug"] == "resident-advisor":
                raw.extend(await scrape_resident_advisor())
            else:
                logger.warning("No scraper registered for source %r", s["slug"])
        except Exception as e:
            logger.error("Scraper for %s raised: %s", s["slug"], e)
        await increment_counters(run_id, sources_scanned=1)

    # Touch last_crawled_at for everything we tried.
    if sources:
        slugs = [s["slug"] for s in sources]
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sources SET last_crawled_at = NOW() WHERE slug = ANY($1::text[])",
                slugs,
            )

    await increment_counters(run_id, releases_scanned=len(raw))
    logger.info("ingest_sources: %d raw releases across %d sources", len(raw), len(sources))

    # Pass through dataclass-as-dict so LangGraph's serializer is happy.
    raw_dicts = [asdict(r) for r in raw]
    return {"sources": sources, "raw_releases": raw_dicts}


async def normalize_releases_node(state: IssueState) -> dict:
    """Dedupe by normalized (artist, title); merge sources_seen for repeats."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="normalizing", current_source=None)

    raw = state.get("raw_releases", [])
    if not raw:
        logger.info("normalize_releases: no raw releases — skipping")
        return {"new_releases": []}

    # Group by (artist_norm, title_norm) inside the run so multi-source
    # mentions become one upsert with the merged sources_seen.
    grouped: dict[tuple[str, str], dict] = {}
    for r in raw:
        artist = (r.get("artist") or "").strip()
        title = (r.get("title") or "").strip()
        artist_norm = normalize(artist)
        title_norm = normalize(title)
        if not artist_norm or not title_norm:
            continue
        key = (artist_norm, title_norm)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = {
                "artist": artist,
                "title": title,
                "artist_normalized": artist_norm,
                "title_normalized": title_norm,
                "label": r.get("label"),
                "catalog_number": r.get("catalog_number"),
                "release_date": r.get("release_date"),
                "url": r.get("url"),
                "cover_art_url": r.get("cover_art_url"),
                "description": r.get("description") or "",
                "sources_seen": [r.get("source_slug")] if r.get("source_slug") else [],
            }
        else:
            # Prefer the longer description (richer context for embedding/prose).
            new_desc = r.get("description") or ""
            if len(new_desc) > len(existing["description"]):
                existing["description"] = new_desc
            slug = r.get("source_slug")
            if slug and slug not in existing["sources_seen"]:
                existing["sources_seen"].append(slug)

    if not grouped:
        return {"new_releases": []}

    pool = await get_pool()
    new_releases: list[dict] = []
    async with pool.acquire() as conn:
        for rec in grouped.values():
            metadata = {
                "description": rec["description"],
                "release_date": rec["release_date"].isoformat()
                if isinstance(rec["release_date"], (date, datetime)) else rec["release_date"],
            }
            row = await conn.fetchrow(
                """
                INSERT INTO releases (
                    title, artist, artist_normalized, title_normalized,
                    label, catalog_number, release_date, url, cover_art_url,
                    metadata, sources_seen, first_seen_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, NOW())
                ON CONFLICT (artist_normalized, title_normalized) DO UPDATE
                  SET sources_seen = (
                          SELECT ARRAY(
                              SELECT DISTINCT unnest(
                                  releases.sources_seen || EXCLUDED.sources_seen
                              )
                          )
                      ),
                      metadata = releases.metadata || EXCLUDED.metadata,
                      cover_art_url = COALESCE(releases.cover_art_url, EXCLUDED.cover_art_url)
                RETURNING id::text, artist, title, artist_normalized, title_normalized,
                          sources_seen, first_seen_at, embedding IS NULL AS needs_embedding,
                          metadata, COALESCE(label, '') AS label,
                          COALESCE(catalog_number, '') AS catalog_number,
                          COALESCE(url, '') AS url,
                          COALESCE(cover_art_url, '') AS cover_art_url,
                          release_date
                """,
                rec["title"], rec["artist"], rec["artist_normalized"], rec["title_normalized"],
                rec["label"], rec["catalog_number"], rec["release_date"], rec["url"],
                rec["cover_art_url"],
                json.dumps(metadata),
                rec["sources_seen"],
            )
            d = dict(row)
            d["description"] = rec["description"]
            new_releases.append(d)

    # Sort by recency + dedup volume; cap candidate set so downstream stages
    # stay bounded. 200 is plenty for a 5-pick selection.
    new_releases.sort(
        key=lambda r: (len(r.get("sources_seen") or []), r.get("first_seen_at") or datetime.min),
        reverse=True,
    )
    new_releases = new_releases[:MAX_CANDIDATES]

    await update_agent_run(run_id, candidates_considered=len(new_releases))
    logger.info("normalize_releases: %d unique releases (capped to %d)",
                len(grouped), len(new_releases))
    return {"new_releases": new_releases}


async def embed_releases_node(state: IssueState) -> dict:
    """Voyage AI embeddings for new releases. Writes embedding column."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="embedding")

    candidates = state.get("new_releases", [])
    needs = [c for c in candidates if c.get("needs_embedding")]
    if not needs:
        return {"embeddings": {}}

    texts = [
        f"{c['artist']} — {c['title']}\n\n{(c.get('description') or '')[:2000]}"
        for c in needs
    ]
    vectors = await embed_texts(texts)

    pool = await get_pool()
    async with pool.acquire() as conn:
        for c, vec in zip(needs, vectors):
            if not vec or all(v == 0 for v in vec):
                continue
            await conn.execute(
                "UPDATE releases SET embedding = $1::vector WHERE id = $2::uuid",
                str(vec), c["id"],
            )
            c["embedding"] = vec
            c["needs_embedding"] = False

    # Backfill `embedding` on rows that already had it stored.
    missing = [c for c in candidates if "embedding" not in c]
    if missing:
        ids = [c["id"] for c in missing]
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id::text, embedding::text FROM releases WHERE id = ANY($1::uuid[])",
                ids,
            )
        by_id = {r["id"]: r["embedding"] for r in rows}
        for c in missing:
            raw_vec = by_id.get(c["id"])
            c["embedding"] = _parse_pgvector(raw_vec) if raw_vec else []

    embeddings = {c["id"]: c.get("embedding") or [] for c in candidates}
    return {"embeddings": embeddings, "new_releases": candidates}


def _parse_pgvector(raw: str | None) -> list[float]:
    """Parse pgvector's '[1.0,2.0,...]' text representation into a list."""
    if not raw:
        return []
    s = raw.strip().lstrip("[").rstrip("]")
    if not s:
        return []
    try:
        return [float(x) for x in s.split(",")]
    except ValueError:
        return []


async def score_for_user_node(state: IssueState) -> dict:
    """Run agent.scoring.score_release for each candidate against the user's
    taste profile. Returns scored list."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="scoring")

    user_id = state["user_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        prof = await conn.fetchrow(
            """
            SELECT seed::text AS seed, tags::text AS tags,
                   source_weights::text AS source_weights,
                   taste_centroid::text AS taste_centroid
              FROM taste_profiles WHERE user_id = $1::uuid
            """,
            user_id,
        )
        recent_rows = await conn.fetch(
            """
            SELECT DISTINCT r.artist
              FROM recommendations rec
              JOIN releases r ON r.id = rec.release_id
              JOIN issues i ON i.id = rec.issue_id
             WHERE i.user_id = $1::uuid
             ORDER BY r.artist
             LIMIT 50
            """,
            user_id,
        )

    if prof is None:
        msg = f"No taste profile for user {user_id}; cannot score."
        logger.error(msg)
        await update_agent_run(run_id, notes=msg)
        return {"scored": [], "taste_profile": {}, "recent_artists": []}

    taste_profile = {
        "tags": json.loads(prof["tags"]) if prof["tags"] else {},
        "source_weights": json.loads(prof["source_weights"]) if prof["source_weights"] else {},
        "taste_centroid": _parse_pgvector(prof["taste_centroid"]),
    }
    recent_artists = [r["artist"] for r in recent_rows]

    sources = state.get("sources", [])
    affinity_by_slug = {s["slug"]: list(s.get("genre_affinity") or []) for s in sources}

    scored: list[dict] = []
    for c in state.get("new_releases", []):
        embedding = c.get("embedding") or []
        sources_seen = list(c.get("sources_seen") or [])
        metadata = c.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        release_tags = _release_tags(metadata or {}, affinity_by_slug, sources_seen)

        result = score_release(
            release_embedding=embedding,
            release_tags=release_tags,
            release_sources=sources_seen,
            release_artist=c["artist"],
            release_first_seen_at=c.get("first_seen_at"),
            taste_centroid=taste_profile["taste_centroid"],
            boosted_tags=taste_profile["tags"],
            source_weights=taste_profile["source_weights"],
            recent_artists=recent_artists,
        )
        scored.append({
            "release_id": c["id"],
            "artist": c["artist"],
            "title": c["title"],
            "score": result["score"],
            "confidence": _confidence_for(result["score"]),
            "components": result["components"],
            "sources_seen": sources_seen,
            "release_tags": release_tags,
        })

    scored.sort(key=lambda s: s["score"], reverse=True)
    await update_agent_run(run_id, candidates_considered=len(scored))
    logger.info("score_for_user: scored %d candidates (top=%.3f)",
                len(scored), scored[0]["score"] if scored else 0.0)
    return {
        "scored": scored,
        "taste_profile": taste_profile,
        "recent_artists": recent_artists,
    }


async def categorize_picks_node(state: IssueState) -> dict:
    """Apply agent.categorization.categorize() to scored candidates.
    Returns 5 picks: 1 Lead, 2 Steady, 1 Stretch, 1 Withheld."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="categorizing")

    scored = state.get("scored", [])
    if not scored:
        return {"picks": []}

    candidates = [
        ScoredCandidate(
            release_id=s["release_id"],
            artist=s["artist"],
            score=s["score"],
            confidence=s["confidence"],
            components=s.get("components", {}),
            sources_seen=s.get("sources_seen", []),
        )
        for s in scored
    ]
    picks = categorize(
        candidates,
        recent_artists=state.get("recent_artists", []),
        source_weights=state.get("taste_profile", {}).get("source_weights", {}),
    )

    # Lead fallback: real-world scores often don't crack 0.85. If categorize()
    # returned no Lead, promote the highest-scoring pick (which will be the
    # top Steady) so the issue still has a Lead. categorize() purposefully
    # leaves this slot empty; the workflow handles the editorial guarantee.
    if picks and not any(p.category == "lead" for p in picks):
        top = max(picks, key=lambda p: p.match_score)
        top.category = "lead"
        top.position = 1
        # Re-pack remaining positions to keep them contiguous and sorted.
        rest = [p for p in picks if p is not top]
        rest.sort(key=lambda p: p.match_score, reverse=True)
        next_pos = 2
        for p in rest:
            if p.category == "withheld":
                p.position = 5
            else:
                p.position = next_pos
                next_pos += 1
        picks = [top, *rest]
        picks.sort(key=lambda p: p.position)

    picks_dicts = [asdict(p) for p in picks]
    await update_agent_run(run_id, records_surfaced=len(picks_dicts))
    logger.info("categorize_picks: %d picks (%s)",
                len(picks_dicts), [p["category"] for p in picks_dicts])
    return {"picks": picks_dicts}


def _scored_lookup(state: IssueState) -> dict[str, dict]:
    return {s["release_id"]: s for s in state.get("scored", [])}


def _release_lookup(state: IssueState) -> dict[str, dict]:
    return {r["id"]: r for r in state.get("new_releases", [])}


async def generate_prose_node(state: IssueState) -> dict:
    """Live (sync) prose generation, parallel across the 5 picks. Production
    runs go through the batch path for the 50% discount; happy-path / dev
    uses live so the issue lands inside one process invocation."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="reasoning", current_source="prose")

    picks = state.get("picks", [])
    if not picks:
        return {"prose": {}, "matched_signals": {}}

    scored = _scored_lookup(state)
    releases = _release_lookup(state)
    user_tags = state.get("taste_profile", {}).get("tags", {})

    contexts: list[dict] = []
    matched: dict[str, list[dict]] = {}
    for p in picks:
        rid = p["release_id"]
        rel = releases.get(rid, {})
        sc = scored.get(rid, {})
        signals = derive_matched_signals(sc.get("release_tags", []), user_tags)
        matched[rid] = signals
        contexts.append({
            "release_id": rid,
            "title": rel.get("title"),
            "artist": rel.get("artist"),
            "label": rel.get("label") or None,
            "catalog_number": rel.get("catalog_number") or None,
            "release_date": rel.get("release_date"),
            "category": p["category"],
            "match_signals": signals,
            "sources_seen": rel.get("sources_seen", []),
            "description_snippet": (rel.get("description") or "")[:600],
        })

    results = await asyncio.gather(
        *(generate_prose_live(c) for c in contexts),
        return_exceptions=True,
    )

    prose: dict[str, dict] = {}
    for ctx, res in zip(contexts, results):
        rid = ctx["release_id"]
        if isinstance(res, Exception):
            logger.error("generate_prose_live raised for %s: %s", rid, res)
            prose[rid] = {"prose": "", "pull_quote": None}
        elif res is None:
            prose[rid] = {"prose": "", "pull_quote": None}
        else:
            prose[rid] = {
                "prose": (res.get("prose") or "").strip(),
                "pull_quote": (res.get("pull_quote") or "").strip() or None,
            }

    return {"prose": prose, "matched_signals": matched}


async def generate_pull_quote_node(state: IssueState) -> dict:
    """If the prose pass already returned a pull quote for the lead, no-op.
    Otherwise, single Haiku call to extract one from the lead's prose."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="pull_quote")

    picks = state.get("picks", [])
    prose = dict(state.get("prose", {}))
    lead = next((p for p in picks if p["category"] == "lead"), None)
    if not lead:
        return {"prose": prose}

    rid = lead["release_id"]
    entry = prose.get(rid) or {}
    if entry.get("pull_quote"):
        return {"prose": prose}

    body = entry.get("prose") or ""
    if not body:
        return {"prose": prose}

    quote = await generate_pull_quote_live(body)
    if quote:
        entry["pull_quote"] = quote
        prose[rid] = entry
    return {"prose": prose}


async def generate_editor_note_node(state: IssueState) -> dict:
    """Single Haiku call. Frame the issue around its throughline."""
    run_id = state["agent_run_id"]
    await update_agent_run(run_id, current_step="editor_note")

    picks = state.get("picks", [])
    if not picks:
        return {"editor_note": "", "issue_title": ""}

    releases = _release_lookup(state)
    matched = state.get("matched_signals", {})
    payload = []
    for p in picks:
        if p["category"] == "withheld":
            continue
        rid = p["release_id"]
        rel = releases.get(rid, {})
        payload.append({
            "category": p["category"],
            "artist": rel.get("artist"),
            "title": rel.get("title"),
            "label": rel.get("label") or None,
            "match_signals": matched.get(rid, []),
        })

    user_tags = state.get("taste_profile", {}).get("tags", {})
    result = await generate_editor_note_live(payload, user_tags=user_tags)
    if not result:
        # Soft fallback so the issue still lands.
        return {
            "editor_note": "",
            "issue_title": f"Issue {datetime.now(timezone.utc).strftime('%b %d')}",
        }
    return {
        "editor_note": (result.get("note") or "").strip(),
        "issue_title": (result.get("title") or "").strip() or "Untitled",
    }


async def persist_issue_node(state: IssueState) -> dict:
    """Write issues + recommendations rows. Bind agent_run_id."""
    run_id = state["agent_run_id"]
    user_id = state["user_id"]
    picks = state.get("picks", [])
    if not picks:
        msg = "persist_issue: no picks — nothing to publish"
        logger.warning(msg)
        await update_agent_run(run_id, notes=msg)
        return {}

    await update_agent_run(run_id, current_step="persisting")

    releases = _release_lookup(state)
    prose = state.get("prose", {})
    matched = state.get("matched_signals", {})
    scored = _scored_lookup(state)

    sources_used: dict[str, int] = {}
    for p in picks:
        for slug in releases.get(p["release_id"], {}).get("sources_seen") or []:
            sources_used[slug] = sources_used.get(slug, 0) + 1

    publish_date = upcoming_sunday()
    withhold_drop_at = friday_drop_at(publish_date)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = None
            if not state.get("force"):
                existing = await conn.fetchrow(
                    """
                    SELECT id::text FROM issues
                     WHERE user_id = $1::uuid AND publish_date = $2
                     LIMIT 1
                    """,
                    user_id, publish_date,
                )
            if existing:
                msg = (
                    f"persist_issue: issue already exists for "
                    f"{publish_date.isoformat()} ({existing['id']}); skipping"
                )
                logger.info(msg)
                await update_agent_run(run_id, notes=msg)
                return {"issue_id": existing["id"]}

            next_num = await conn.fetchval(
                """
                SELECT COALESCE(MAX(issue_number), 0) + 1
                  FROM issues WHERE user_id = $1::uuid
                """,
                user_id,
            )

            title = state.get("issue_title") or f"Issue {next_num}"
            editor_note = state.get("editor_note") or ""

            issue_row = await conn.fetchrow(
                """
                INSERT INTO issues (
                    user_id, issue_number, volume, publish_date, status,
                    title, editor_note, sources_used, agent_run_id
                )
                VALUES ($1::uuid, $2, 1, $3, 'draft', $4, $5, $6::jsonb, $7::uuid)
                RETURNING id::text
                """,
                user_id, next_num, publish_date, title, editor_note,
                json.dumps(sources_used), run_id,
            )
            issue_id = issue_row["id"]

            for p in picks:
                rid = p["release_id"]
                rel = releases.get(rid, {})
                pr = prose.get(rid, {})
                signals = matched.get(rid, [])
                sc = scored.get(rid, {})
                cover = rel.get("cover_art_url") or None
                withhold_until = (
                    withhold_drop_at if p["category"] == "withheld" else None
                )

                await conn.execute(
                    """
                    INSERT INTO recommendations (
                        issue_id, release_id, position, category,
                        match_score, confidence, source_attr, prose,
                        pull_quote, matched_signals, cover_art_url, withhold_until
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4::recommendation_category,
                            $5, $6::confidence_level, $7, $8, $9,
                            $10::jsonb, $11, $12)
                    """,
                    issue_id, rid, p["position"], p["category"],
                    round(p.get("match_score") or sc.get("score") or 0.0, 3),
                    p.get("confidence") or sc.get("confidence") or "low",
                    p.get("source_attr") or "unknown",
                    pr.get("prose") or "",
                    pr.get("pull_quote"),
                    json.dumps(signals),
                    cover,
                    withhold_until,
                )

    await mark_completed(run_id, issue_id=issue_id)
    logger.info("persist_issue: issue %s (id=%s) published with %d recs",
                next_num, issue_id, len(picks))
    return {"issue_id": issue_id}


async def render_email_node(state: IssueState) -> dict:
    """MJML render. Stub for happy-path; lands in Sprint Week 6."""
    logger.info("[stub] render_email — Week 6 deliverable")
    return {}


async def send_email_node(state: IssueState) -> dict:
    """Resend send. Stub for happy-path; lands in Sprint Week 6."""
    logger.info("[stub] send_email — Week 6 deliverable")
    return {}


# ── Build the graph ───────────────────────────────────────────────────────

def build_issue_graph():
    g = StateGraph(IssueState)

    g.add_node("ingest_sources", ingest_sources_node)
    g.add_node("normalize_releases", normalize_releases_node)
    g.add_node("embed_releases", embed_releases_node)
    g.add_node("score_for_user", score_for_user_node)
    g.add_node("categorize_picks", categorize_picks_node)
    g.add_node("generate_prose", generate_prose_node)
    g.add_node("generate_pull_quote", generate_pull_quote_node)
    g.add_node("generate_editor_note", generate_editor_note_node)
    g.add_node("persist_issue", persist_issue_node)
    g.add_node("render_email", render_email_node)
    g.add_node("send_email", send_email_node)

    g.set_entry_point("ingest_sources")
    g.add_edge("ingest_sources", "normalize_releases")
    g.add_edge("normalize_releases", "embed_releases")
    g.add_edge("embed_releases", "score_for_user")
    g.add_edge("score_for_user", "categorize_picks")
    g.add_edge("categorize_picks", "generate_prose")
    g.add_edge("generate_prose", "generate_pull_quote")
    g.add_edge("generate_pull_quote", "generate_editor_note")
    g.add_edge("generate_editor_note", "persist_issue")
    g.add_edge("persist_issue", "render_email")
    g.add_edge("render_email", "send_email")
    g.add_edge("send_email", END)

    return g.compile()


issue_pipeline = build_issue_graph()
