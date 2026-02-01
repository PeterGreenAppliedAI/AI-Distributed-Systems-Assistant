#!/usr/bin/env python3
"""
Backfill embeddings for existing log_events rows that have NULL embedding_vector.

Queries rows in batches, calls the LLM gateway batch endpoint for embeddings,
and updates each row. Can be stopped and resumed safely (queries by NULL).

Usage:
    python scripts/backfill_embeddings.py --batch-size 50
"""

import os
import sys
import time
import argparse

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.database import get_sync_connection

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://192.168.1.184:8001")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "120"))


def _vec_to_text(vec: list[float]) -> str:
    return "[" + ",".join(str(f) for f in vec) + "]"


def embed_batch_sync(client: httpx.Client, texts: list[str]) -> list[list[float] | None]:
    """Embed a batch of texts via the OpenAI-compatible endpoint."""
    try:
        resp = client.post(
            f"{GATEWAY_URL}/v1/embeddings",
            json={"model": EMBEDDING_MODEL, "input": texts},
            timeout=EMBEDDING_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data]
    except Exception as e:
        print(f"  Batch embedding failed: {e}")
        return [None] * len(texts)


def backfill(batch_size: int, delay: float = 0.0):
    conn = get_sync_connection()
    client = httpx.Client()
    total_updated = 0
    total_failed = 0
    t_start = time.time()

    # Find resume point: max embedded ID (fast index scan)
    with conn.cursor() as cursor:
        cursor.execute("SELECT COALESCE(MAX(id), 0) as last_id "
                       "FROM log_events WHERE embedding_vector IS NOT NULL")
        last_id = cursor.fetchone()["last_id"]
    print(f"Resuming from id > {last_id}")

    try:
        while True:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, message FROM log_events "
                    "WHERE id > %s AND embedding_vector IS NULL "
                    "ORDER BY id LIMIT %s",
                    (last_id, batch_size),
                )
                rows = cursor.fetchall()

            if not rows:
                elapsed = time.time() - t_start
                print(f"Done. Updated {total_updated} rows, {total_failed} failures in {elapsed:.0f}s.")
                break

            messages = [row["message"] for row in rows]
            embeddings = embed_batch_sync(client, messages)

            batch_updated = 0
            for row, embedding in zip(rows, embeddings):
                if embedding is None:
                    total_failed += 1
                    continue

                vec_text = _vec_to_text(embedding)
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE log_events SET embedding_vector = VEC_FromText(%s) WHERE id = %s",
                        (vec_text, row["id"]),
                    )
                batch_updated += 1

            conn.commit()
            total_updated += batch_updated
            last_id = rows[-1]["id"]

            elapsed = time.time() - t_start
            rate = total_updated / elapsed if elapsed > 0 else 0
            print(f"Batch done: +{batch_updated} rows | Total: {total_updated} | "
                  f"Failed: {total_failed} | Rate: {rate:.1f} rows/s | "
                  f"Elapsed: {elapsed:.0f}s | Last id: {last_id}")

            if delay > 0:
                time.sleep(delay)

    except KeyboardInterrupt:
        conn.commit()
        elapsed = time.time() - t_start
        print(f"\nInterrupted. Updated {total_updated} rows in {elapsed:.0f}s. Resume to continue.")
    finally:
        client.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill embedding vectors for log_events")
    parser.add_argument("--batch-size", type=int, default=50, help="Rows per batch (default 50)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds to sleep between batches for thermal cooldown (default 0)")
    args = parser.parse_args()

    print(f"Backfilling embeddings (model={EMBEDDING_MODEL}, batch_size={args.batch_size}, delay={args.delay}s)")
    print(f"Gateway: {GATEWAY_URL}")
    backfill(args.batch_size, args.delay)
