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
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

# Note: importing langgraph triggers a `LangChainPendingDeprecationWarning`
# about `allowed_objects` defaulting changing. We don't construct the
# affected JsonPlusSerializer directly (langgraph does, internally), and
# the warning bypasses standard `warnings.filterwarnings` / `catch_warnings`
# suppression — likely emitted via a custom langchain mechanism. Living
# with the noise rather than monkey-patching warnings.warn.
from langgraph.graph import END, StateGraph

logger = logging.getLogger("cratedigger-agent.workflows.issue")


class IssueState(TypedDict, total=False):
    """LangGraph state for one weekly issue run."""
    user_id: str
    agent_run_id: str
    force: bool
    # Ingestion
    sources: list[dict]           # rows from sources table
    raw_releases: list[dict]      # all crawler output
    new_releases: list[dict]      # post-dedup
    embeddings: dict[str, list[float]]  # release_id → vector
    # Scoring + categorization
    taste_profile: dict[str, Any]
    scored: list[dict]            # candidates with scores
    picks: list[dict]             # 5 categorized picks
    # Reasoning
    prose: dict[str, dict]        # release_id → {prose, pull_quote?}
    matched_signals: dict[str, list[dict]]  # release_id → signals
    editor_note: str
    # Persistence
    issue_id: str
    # Diagnostics
    errors: list[str]


# ── Node stubs ────────────────────────────────────────────────────────────
# Each will be filled in across weeks 2–4. Returns partial state updates.

async def ingest_sources_node(state: IssueState) -> dict:
    """Fetch from all active sources (RSS + scrape). Writes agent_runs row
    with current_step='ingesting' and current_source per crawler step."""
    logger.info("[stub] ingest_sources")
    return {"raw_releases": []}


async def normalize_releases_node(state: IssueState) -> dict:
    """Dedupe by normalized (artist, title); merge sources_seen for repeats."""
    logger.info("[stub] normalize_releases")
    return {"new_releases": []}


async def embed_releases_node(state: IssueState) -> dict:
    """Voyage AI embeddings for new releases. Writes embedding column."""
    logger.info("[stub] embed_releases")
    return {"embeddings": {}}


async def score_for_user_node(state: IssueState) -> dict:
    """Run agent.scoring.score_release for each candidate against the user's
    taste profile. Returns scored list."""
    logger.info("[stub] score_for_user")
    return {"scored": []}


async def categorize_picks_node(state: IssueState) -> dict:
    """Apply agent.categorization.categorize() to scored candidates.
    Returns 5 picks: 1 Lead, 2 Steady, 1 Stretch, 1 Withheld."""
    logger.info("[stub] categorize_picks")
    return {"picks": []}


async def generate_prose_node(state: IssueState) -> dict:
    """Submit prose generation to Anthropic Message Batches (50% discount).
    Returns immediately; the batch_poller writes results when ready.

    Until the batch lands, the issue ships with prose=NULL — frontend
    tolerates NULL gracefully (per the Sift defensive-degradation pattern).
    """
    logger.info("[stub] generate_prose")
    return {"prose": {}}


async def generate_pull_quote_node(state: IssueState) -> dict:
    """Single-shot call: extract a 4–8 word pull quote from the lead's prose.
    Live (not batched) since it depends on the lead prose having returned."""
    logger.info("[stub] generate_pull_quote")
    return {}


async def generate_editor_note_node(state: IssueState) -> dict:
    """Single Haiku call. Frame the issue around its throughline ('the
    throughline this week is patience…')."""
    logger.info("[stub] generate_editor_note")
    return {"editor_note": ""}


async def persist_issue_node(state: IssueState) -> dict:
    """Write issues + recommendations rows. Bind agent_run_id."""
    logger.info("[stub] persist_issue")
    return {}


async def render_email_node(state: IssueState) -> dict:
    """MJML render of the issue. Stash result in issues.email_html for replay."""
    logger.info("[stub] render_email")
    return {}


async def send_email_node(state: IssueState) -> dict:
    """Send via Resend. Mark issue.status='delivered' on success."""
    logger.info("[stub] send_email")
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
