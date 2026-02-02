#!/usr/bin/env python3
"""
Cron safety net: catches log_events with template_id IS NULL.

Canonicalizes, checks if template exists, creates if needed, links.
Designed to run every 6 hours via cron.

Usage:
    python scripts/cron_template_safety_net.py --batch-size 100 --delay 2

Cron entry:
    0 */6 * * * cd /home/tadeu718/devmesh-platform && python3 scripts/cron_template_safety_net.py --batch-size 100 --delay 2 >> /var/log/devmesh-template-safety.log 2>&1
"""

import os
import sys
import json
import time
import hashlib
import argparse

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.database import get_sync_connection
from services.canonicalize import canonicalize, canon_hash, CANON_VERSION

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://192.168.1.184:8001")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
EMBEDDING_TIMEOUT = int(os.getenv("EMBEDDING_TIMEOUT", "120"))
MAX_ROWS = 10000  # Safety cap per run


def _vec_to_text(vec: list[float]) -> str:
    return "[" + ",".join(str(f) for f in vec) + "]"


def embed_batch_sync(client: httpx.Client, texts: list[str]) -> list[list[float] | None]:
    if not texts:
        return []
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


def run_safety_net(batch_size: int, delay: float = 0.0):
    conn = get_sync_connection()
    http_client = httpx.Client()
    total_linked = 0
    total_new = 0
    t_start = time.time()

    # Load existing templates
    template_map: dict[str, int] = {}
    with conn.cursor() as cursor:
        cursor.execute("SELECT template_hash, id FROM log_templates")
        for row in cursor.fetchall():
            template_map[row["template_hash"]] = row["id"]
    print(f"Loaded {len(template_map)} existing templates")

    # Count orphans
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM log_events WHERE template_id IS NULL"
        )
        orphan_count = cursor.fetchone()["cnt"]
    print(f"Found {orphan_count} orphaned log_events (template_id IS NULL)")

    if orphan_count == 0:
        print("Nothing to do.")
        conn.close()
        return

    last_id = 0
    rows_processed = 0

    try:
        while rows_processed < MAX_ROWS:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, message, service, level, host "
                    "FROM log_events "
                    "WHERE id > %s AND template_id IS NULL "
                    "ORDER BY id LIMIT %s",
                    (last_id, batch_size),
                )
                rows = cursor.fetchall()

            if not rows:
                break

            # Canonicalize and find new hashes
            batch_keys = []
            new_hashes: dict[str, tuple[str, str, str, str]] = {}

            for row in rows:
                canonical = canonicalize(row["message"])
                t_hash = canon_hash(canonical, row["service"], row["level"])
                batch_keys.append((row, t_hash))

                if t_hash not in template_map and t_hash not in new_hashes:
                    new_hashes[t_hash] = (
                        canonical, row["service"], row["level"], row["host"]
                    )

            # Embed and insert new templates (with retry on row conflict/deadlock)
            if new_hashes:
                hashes_list = list(new_hashes.keys())
                texts_list = [new_hashes[h][0] for h in hashes_list]
                embeddings = embed_batch_sync(http_client, texts_list)

                for t_hash, emb in zip(hashes_list, embeddings):
                    if emb is None:
                        continue
                    canonical, service, level, host = new_hashes[t_hash]
                    canon_hash_val = hashlib.sha256(canonical.encode()).hexdigest()[:32]
                    vec_text = _vec_to_text(emb)

                    for attempt in range(3):
                        try:
                            with conn.cursor() as cursor:
                                cursor.execute("""
                                    INSERT INTO log_templates
                                        (template_hash, canonical_text, service, level,
                                         embedding_vector, canon_version, canon_hash,
                                         first_seen, last_seen, event_count, source_hosts)
                                    VALUES (%s, %s, %s, %s, VEC_FromText(%s), %s, %s,
                                            NOW(6), NOW(6), 0, %s)
                                    ON DUPLICATE KEY UPDATE id=id
                                """, (
                                    t_hash, canonical, service, level,
                                    vec_text, CANON_VERSION, canon_hash_val,
                                    json.dumps([host]),
                                ))
                                new_id = cursor.lastrowid
                            conn.commit()
                            if new_id:
                                template_map[t_hash] = new_id
                                total_new += 1
                            else:
                                with conn.cursor() as cursor:
                                    cursor.execute(
                                        "SELECT id FROM log_templates WHERE template_hash = %s",
                                        (t_hash,),
                                    )
                                    existing = cursor.fetchone()
                                    if existing:
                                        template_map[t_hash] = existing["id"]
                            break
                        except Exception as e:
                            conn.rollback()
                            if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                                time.sleep(0.2)
                            else:
                                print(f"  Template insert failed for {t_hash}: {e}")
                                break

            # Link events
            with conn.cursor() as cursor:
                for row, t_hash in batch_keys:
                    tid = template_map.get(t_hash)
                    if tid is None:
                        continue
                    cursor.execute(
                        "UPDATE log_events SET template_id = %s WHERE id = %s",
                        (tid, row["id"]),
                    )
                    total_linked += 1
            conn.commit()

            rows_processed += len(rows)
            last_id = rows[-1]["id"]

            elapsed = time.time() - t_start
            print(f"Batch: +{len(rows)} | Total: {rows_processed} | "
                  f"New templates: {total_new} | Linked: {total_linked} | "
                  f"Elapsed: {elapsed:.0f}s")

            if delay > 0:
                time.sleep(delay)

    except KeyboardInterrupt:
        conn.commit()
        print(f"\nInterrupted at {rows_processed} rows processed.")
    finally:
        http_client.close()
        conn.close()

    elapsed = time.time() - t_start
    print(f"\nSafety net complete. Processed {rows_processed} rows, "
          f"{total_new} new templates, {total_linked} linked in {elapsed:.0f}s.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cron safety net for orphaned log_events"
    )
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Rows per batch (default 100)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between batches (default 2)")
    args = parser.parse_args()

    print(f"Template safety net (batch_size={args.batch_size}, delay={args.delay}s)")
    run_safety_net(args.batch_size, args.delay)
