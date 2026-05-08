"""Voyage AI embedder. Harvested from sift-api/services/embedder.py.

Differences from Sift:
  - MODEL = "voyage-3" (was "voyage-3-lite")
  - DIM = 1024 (was 512)
  Per SPEC.md §2 / §3.

The fallback zero-vector path is preserved — better to have a placeholder
than fail the entire pipeline on an embedding outage.
"""
from __future__ import annotations

import asyncio
import logging

import voyageai

from agent.config import settings

logger = logging.getLogger("cratedigger-agent.embedder")

MODEL = "voyage-3"
DIM = 1024
BATCH_SIZE = 128  # Voyage AI max batch size


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using Voyage AI voyage-3.

    Returns list of 1024-dim vectors in the same order as inputs.
    """
    if not texts:
        return []

    client = voyageai.Client(api_key=settings.voyage_api_key)
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        try:
            # voyageai.Client.embed is synchronous; run in thread pool
            result = await asyncio.to_thread(
                client.embed,
                batch,
                model=MODEL,
                input_type="document",
            )
            all_embeddings.extend(result.embeddings)
        except Exception as e:
            logger.error("Embedding failed for batch %d: %s", i // BATCH_SIZE, e)
            # Zero-vector fallback so the pipeline can continue
            all_embeddings.extend([[0.0] * DIM for _ in batch])

    logger.info("Embedded %d texts (%d-dim vectors)", len(all_embeddings), DIM)
    return all_embeddings


async def embed_query(text: str) -> list[float]:
    """Single-text embedding for query-time scoring (e.g. taste centroid construction)."""
    if not text:
        return [0.0] * DIM

    client = voyageai.Client(api_key=settings.voyage_api_key)
    try:
        result = await asyncio.to_thread(
            client.embed,
            [text],
            model=MODEL,
            input_type="query",
        )
        return result.embeddings[0]
    except Exception as e:
        logger.error("Query embedding failed: %s", e)
        return [0.0] * DIM
