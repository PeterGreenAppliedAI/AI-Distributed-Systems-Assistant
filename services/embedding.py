"""
DevMesh Platform - Embedding Service
Async client for LLM gateway embedding API (Qwen3-Embedding:8b).

Uses the OpenAI-compatible /v1/embeddings endpoint for batch support.
Falls back to single-text /api/embeddings (Ollama native) for embed_text().
"""

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://192.168.1.184:8001")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "120"))


async def embed_text(
    client: httpx.AsyncClient, text: str
) -> Optional[list[float]]:
    """Embed a single text string. Returns None on failure."""
    try:
        resp = await client.post(
            f"{GATEWAY_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=EMBEDDING_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        logger.warning("Embedding failed for text (len=%d): %s", len(text), e)
        return None


async def embed_batch(
    client: httpx.AsyncClient, texts: list[str]
) -> list[Optional[list[float]]]:
    """Embed a list of texts via the OpenAI-compatible batch endpoint.

    Uses /v1/embeddings which accepts multiple inputs in one request.
    Returns list of embeddings (None for the entire batch on failure,
    falls back to sequential single-text calls).
    """
    if not texts:
        return []

    try:
        resp = await client.post(
            f"{GATEWAY_URL}/v1/embeddings",
            json={"model": EMBEDDING_MODEL, "input": texts},
            timeout=EMBEDDING_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Sort by index to guarantee order matches input
        data.sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data]
    except Exception as e:
        logger.warning("Batch embedding failed, falling back to sequential: %s", e)
        # Fall back to one-at-a-time
        results: list[Optional[list[float]]] = []
        for text in texts:
            results.append(await embed_text(client, text))
        return results
