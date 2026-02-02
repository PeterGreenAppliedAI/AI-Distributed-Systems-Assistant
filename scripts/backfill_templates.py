#!/usr/bin/env python3
"""
Backfill log_events into log_templates.

Processes existing log_events rows:
1. Canonicalize each message, compute template_hash
2. If template exists: UPDATE log_events SET template_id, increment counter
3. If new template: embed canonical text, insert into log_templates, then update log_events
4. ID-based cursor, resumable, configurable batch-size and delay

Usage:
    python scripts/backfill_templates.py --batch-size 50 --delay 2
    python scripts/backfill_templates.py --canon-version v2   # re-canonicalize
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


def _vec_to_text(vec: list[float]) -> str:
    return "[" + ",".join(str(f) for f in vec) + "]"


def embed_batch_sync(client: httpx.Client, texts: list[str]) -> list[list[float] | None]:
    """Embed a batch of texts via the OpenAI-compatible endpoint."""
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


def backfill(batch_size: int, delay: float = 0.0, version: str = CANON_VERSION):
    conn = get_sync_connection()
    http_client = httpx.Client()
    total_processed = 0
    total_new_templates = 0
    total_linked = 0
    total_failed = 0
    t_start = time.time()

    # Find resume point: scan forward from the first NULL template_id row
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(MIN(id) - 1, 0) as last_id "
            "FROM log_events WHERE template_id IS NULL"
        )
        last_id = cursor.fetchone()["last_id"]
    print(f"Resuming from id > {last_id}")

    # Load existing template hashes into memory
    template_map: dict[str, int] = {}
    with conn.cursor() as cursor:
        cursor.execute("SELECT template_hash, id FROM log_templates")
        for row in cursor.fetchall():
            template_map[row["template_hash"]] = row["id"]
    print(f"Loaded {len(template_map)} existing templates into memory")

    try:
        while True:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, message, service, level, host, timestamp "
                    "FROM log_events "
                    "WHERE id > %s AND template_id IS NULL "
                    "ORDER BY id LIMIT %s",
                    (last_id, batch_size),
                )
                rows = cursor.fetchall()

            if not rows:
                elapsed = time.time() - t_start
                print(f"\nDone. Processed {total_processed} rows, "
                      f"{total_new_templates} new templates, "
                      f"{total_linked} linked, {total_failed} failed "
                      f"in {elapsed:.0f}s.")
                break

            # Canonicalize and group by template_hash
            batch_keys = []
            new_hashes_to_embed: dict[str, tuple[str, str, str, str]] = {}

            for row in rows:
                canonical = canonicalize(row["message"], version=version)
                t_hash = canon_hash(canonical, row["service"], row["level"])
                batch_keys.append((row, t_hash, canonical))

                if t_hash not in template_map and t_hash not in new_hashes_to_embed:
                    new_hashes_to_embed[t_hash] = (
                        canonical, row["service"], row["level"], row["host"]
                    )

            # Embed and insert new templates (with retry on row conflict)
            if new_hashes_to_embed:
                hashes_list = list(new_hashes_to_embed.keys())
                texts_list = [new_hashes_to_embed[h][0] for h in hashes_list]
                embeddings = embed_batch_sync(http_client, texts_list)

                for t_hash, emb in zip(hashes_list, embeddings):
                    if emb is None:
                        total_failed += 1
                        continue

                    canonical, service, level, host = new_hashes_to_embed[t_hash]
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
                                    vec_text, version, canon_hash_val,
                                    json.dumps([host]),
                                ))
                                new_id = cursor.lastrowid
                            conn.commit()
                            if new_id:
                                template_map[t_hash] = new_id
                                total_new_templates += 1
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

            # Link log_events to templates
            hash_counts: dict[str, int] = {}
            with conn.cursor() as cursor:
                for row, t_hash, canonical in batch_keys:
                    tid = template_map.get(t_hash)
                    if tid is None:
                        continue
                    cursor.execute(
                        "UPDATE log_events SET template_id = %s WHERE id = %s",
                        (tid, row["id"]),
                    )
                    total_linked += 1
                    hash_counts[t_hash] = hash_counts.get(t_hash, 0) + 1
            conn.commit()

            # Update event_count (separate transaction, retry on row conflict)
            for t_hash, cnt in hash_counts.items():
                tid = template_map.get(t_hash)
                if not tid:
                    continue
                for attempt in range(3):
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute(
                                "UPDATE log_templates SET event_count = event_count + %s "
                                "WHERE id = %s",
                                (cnt, tid),
                            )
                        conn.commit()
                        break
                    except Exception as e:
                        conn.rollback()
                        if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                            time.sleep(0.2)
                        else:
                            print(f"  Counter update failed for {t_hash}: {e}")
                            break

            total_processed += len(rows)
            last_id = rows[-1]["id"]

            elapsed = time.time() - t_start
            rate = total_processed / elapsed if elapsed > 0 else 0
            print(f"Batch: +{len(rows)} rows | Total: {total_processed} | "
                  f"New templates: {total_new_templates} | Linked: {total_linked} | "
                  f"Rate: {rate:.1f} rows/s | Last id: {last_id}")

            if delay > 0:
                time.sleep(delay)

    except KeyboardInterrupt:
        conn.commit()
        elapsed = time.time() - t_start
        print(f"\nInterrupted. Processed {total_processed} rows, "
              f"{total_new_templates} new templates in {elapsed:.0f}s. Resume to continue.")
    finally:
        http_client.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill log_events into log_templates"
    )
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Rows per batch (default 50)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds between batches for thermal cooldown (default 0)")
    parser.add_argument("--canon-version", type=str, default=CANON_VERSION,
                        help=f"Canonicalization version (default {CANON_VERSION})")
    args = parser.parse_args()

    print(f"Backfilling templates (model={EMBEDDING_MODEL}, "
          f"batch_size={args.batch_size}, delay={args.delay}s, "
          f"canon_version={args.canon_version})")
    print(f"Gateway: {GATEWAY_URL}")
    backfill(args.batch_size, args.delay, args.canon_version)
