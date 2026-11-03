#!/usr/bin/env python3
"""
Re-canonicalize and consolidate log_templates after rule updates.

When canonicalization rules change (e.g. adding session ID, veth, GIN fixes),
existing templates may become duplicates under the new rules. This script:

1. Loads all templates, re-canonicalizes each canonical_text
2. Groups by new template_hash — duplicates form merge groups
3. For each merge group: keeps one survivor, re-links log_events, deletes rest
4. Re-embeds only survivors whose canonical text actually changed
5. Updates event_count on survivors

Usage:
    python scripts/reconsolidate_templates.py --delay 2
    python scripts/reconsolidate_templates.py --dry-run   # preview only
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


API_HEALTH_URL = os.getenv("RECONSOLIDATE_API_HEALTH_URL", "http://localhost:8000/health")


def _api_is_running() -> bool:
    try:
        httpx.get(API_HEALTH_URL, timeout=2)
        return True
    except Exception:
        return False


RECOUNT_SQL = (
    "UPDATE log_templates t "
    "LEFT JOIN (SELECT template_id, COUNT(*) AS c FROM log_events "
    "           WHERE template_id IS NOT NULL GROUP BY template_id) x "
    "  ON x.template_id = t.id "
    "SET t.event_count = COALESCE(x.c, 0)"
)


def recount_only() -> None:
    """Recompute log_templates.event_count from log_events.

    Safe with the API running: it never changes template ids or hashes, so the
    API's TemplateCache stays valid. Heals counter drift from concurrent
    ingests or failed counter updates without an outage window.

    Under live ingest the result is a point-in-time snapshot. Increments that
    land during the recount may or may not be included, so treat event_count
    as approximate. One GROUP BY pass over log_events, not a correlated
    subquery per template.
    """
    conn = get_sync_connection()
    with conn.cursor() as cur:
        cur.execute(RECOUNT_SQL)
        print(f"Recounted event_count on {cur.rowcount} templates")
    conn.commit()
    conn.close()


def reconsolidate(delay: float = 2.0, dry_run: bool = False, embed_batch_size: int = 20):
    conn = get_sync_connection()
    http_client = httpx.Client()
    t_start = time.time()

    # Load all templates
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, template_hash, canonical_text, service, level, "
            "event_count, source_hosts, first_seen, last_seen FROM log_templates"
        )
        templates = cur.fetchall()
    print(f"Loaded {len(templates)} templates")

    # Re-canonicalize and group by new hash
    new_groups: dict[str, list[dict]] = {}
    changed_count = 0

    for t in templates:
        new_canon = canonicalize(t["canonical_text"])
        new_hash = canon_hash(new_canon, t["service"], t["level"])
        t["_new_canon"] = new_canon
        t["_new_hash"] = new_hash
        t["_changed"] = (new_canon != t["canonical_text"])
        if t["_changed"]:
            changed_count += 1

        if new_hash not in new_groups:
            new_groups[new_hash] = []
        new_groups[new_hash].append(t)

    merge_groups = {k: v for k, v in new_groups.items() if len(v) > 1}
    solo_changed = {k: v[0] for k, v in new_groups.items()
                    if len(v) == 1 and v[0]["_changed"]}

    templates_to_delete = sum(len(v) - 1 for v in merge_groups.values())

    print(f"Changed canonical text: {changed_count}")
    print(f"Merge groups:          {len(merge_groups)}")
    print(f"Templates to delete:   {templates_to_delete}")
    print(f"Templates after:       {len(templates) - templates_to_delete}")
    print(f"Solo updates (text):   {len(solo_changed)}")
    print(f"Embeddings needed:     {len(solo_changed) + len(merge_groups)}")

    if dry_run:
        print("\n--- DRY RUN — no changes made ---")
        print("\nTop 15 merge groups:")
        for k, v in sorted(merge_groups.items(), key=lambda x: len(x[1]), reverse=True)[:15]:
            total_events = sum(r["event_count"] for r in v)
            print(f"  {len(v)} -> 1 ({total_events} events): "
                  f"{v[0]['_new_canon'][:100]}")
        conn.close()
        http_client.close()
        return

    # Phase 1: Merge groups — pick survivor, re-link events, delete losers
    total_relinked = 0
    total_relink_failures = 0
    total_deleted = 0
    total_embedded = 0

    print(f"\n--- Phase 1: Merging {len(merge_groups)} groups ---")
    for i, (new_hash, group) in enumerate(
        sorted(merge_groups.items(), key=lambda x: len(x[1]), reverse=True)
    ):
        # Survivor = highest event_count (most established)
        group.sort(key=lambda r: r["event_count"], reverse=True)
        survivor = group[0]
        victims = group[1:]
        victim_ids = [v["id"] for v in victims]
        new_canon = survivor["_new_canon"]
        total_events = sum(r["event_count"] for r in group)
        merged_first = min(r["first_seen"] for r in group)
        merged_last = max(r["last_seen"] for r in group)
        merged_hosts: list[str] = []
        for r in group:
            try:
                hosts = json.loads(r["source_hosts"] or "[]")
            except (TypeError, ValueError):
                hosts = []
            for h in hosts:
                if h not in merged_hosts:
                    merged_hosts.append(h)

        # Re-link log_events from victims to survivor. A victim is only
        # deleted if its re-link succeeded — otherwise events would point at
        # a deleted template id (there is no FK to catch it).
        relinked_ok: list[int] = []
        for vid in victim_ids:
            for attempt in range(3):
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE log_events SET template_id = %s "
                            "WHERE template_id = %s",
                            (survivor["id"], vid),
                        )
                        total_relinked += cur.rowcount
                    conn.commit()
                    relinked_ok.append(vid)
                    break
                except Exception as e:
                    conn.rollback()
                    if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                        time.sleep(0.2)
                    else:
                        print(f"  Re-link failed for victim {vid}: {e} — keeping it")
                        break
        failed_relinks = [v for v in victim_ids if v not in relinked_ok]
        if failed_relinks:
            total_relink_failures += len(failed_relinks)

        # Delete only victims whose events were re-linked
        for vid in relinked_ok:
            for attempt in range(3):
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM log_templates WHERE id = %s", (vid,)
                        )
                    conn.commit()
                    total_deleted += 1
                    break
                except Exception as e:
                    conn.rollback()
                    if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                        time.sleep(0.2)
                    else:
                        print(f"  Delete failed for {vid}: {e}")
                        break

        # Update survivor: new canonical text, new hash, new event_count
        canon_hash_val = hashlib.sha256(new_canon.encode()).hexdigest()[:32]
        for attempt in range(3):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE log_templates SET canonical_text = %s, "
                        "template_hash = %s, canon_hash = %s, "
                        "event_count = %s, first_seen = %s, last_seen = %s, "
                        "source_hosts = %s WHERE id = %s",
                        (new_canon, new_hash, canon_hash_val,
                         total_events, merged_first, merged_last,
                         json.dumps(merged_hosts), survivor["id"]),
                    )
                conn.commit()
                break
            except Exception as e:
                conn.rollback()
                if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                    time.sleep(0.2)
                else:
                    print(f"  Survivor update failed for {survivor['id']}: {e}")
                    break

        if (i + 1) % 10 == 0 or (i + 1) == len(merge_groups):
            elapsed = time.time() - t_start
            print(f"  Merged {i+1}/{len(merge_groups)} groups | "
                  f"Deleted: {total_deleted} | Re-linked: {total_relinked} | "
                  f"{elapsed:.0f}s")

    # Phase 2: Update solo templates whose text changed
    print(f"\n--- Phase 2: Updating {len(solo_changed)} solo templates ---")
    solo_updated = 0
    for new_hash, t in solo_changed.items():
        new_canon = t["_new_canon"]
        canon_hash_val = hashlib.sha256(new_canon.encode()).hexdigest()[:32]
        for attempt in range(3):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE log_templates SET canonical_text = %s, "
                        "template_hash = %s, canon_hash = %s WHERE id = %s",
                        (new_canon, new_hash, canon_hash_val, t["id"]),
                    )
                conn.commit()
                solo_updated += 1
                break
            except Exception as e:
                conn.rollback()
                if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                    time.sleep(0.2)
                else:
                    print(f"  Solo update failed for {t['id']}: {e}")
                    break
    print(f"  Updated {solo_updated} solo templates")

    # Phase 3: Re-embed all templates whose text changed
    # Collect IDs that need re-embedding
    needs_embed = []
    # Survivors from merge groups
    for new_hash, group in merge_groups.items():
        group.sort(key=lambda r: r["event_count"], reverse=True)
        needs_embed.append((group[0]["id"], group[0]["_new_canon"]))
    # Solo changed
    for new_hash, t in solo_changed.items():
        needs_embed.append((t["id"], t["_new_canon"]))

    print(f"\n--- Phase 3: Re-embedding {len(needs_embed)} templates ---")
    for batch_start in range(0, len(needs_embed), embed_batch_size):
        batch = needs_embed[batch_start:batch_start + embed_batch_size]
        ids = [b[0] for b in batch]
        texts = [b[1] for b in batch]

        embeddings = embed_batch_sync(http_client, texts)

        for tid, emb in zip(ids, embeddings):
            if emb is None:
                continue
            vec_text = _vec_to_text(emb)
            for attempt in range(3):
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE log_templates SET embedding_vector = "
                            "VEC_FromText(%s) WHERE id = %s",
                            (vec_text, tid),
                        )
                    conn.commit()
                    total_embedded += 1
                    break
                except Exception as e:
                    conn.rollback()
                    if attempt < 2 and ("1020" in str(e) or "1213" in str(e)):
                        time.sleep(0.2)
                    else:
                        print(f"  Embed update failed for {tid}: {e}")
                        break

        elapsed = time.time() - t_start
        print(f"  Embedded {min(batch_start + embed_batch_size, len(needs_embed))}/"
              f"{len(needs_embed)} | {elapsed:.0f}s")

        if delay > 0:
            time.sleep(delay)

    # Phase 4: Fix event_count on all remaining templates
    print("\n--- Phase 4: Correcting event_counts ---")
    with conn.cursor() as cur:
        cur.execute(RECOUNT_SQL)
        fixed = cur.rowcount
    conn.commit()
    print(f"  Updated event_count on {fixed} templates")

    elapsed = time.time() - t_start
    print(f"\nDone in {elapsed:.0f}s.")
    print(f"  Merged: {len(merge_groups)} groups")
    print(f"  Deleted: {total_deleted} redundant templates")
    print(f"  Re-linked: {total_relinked} events")
    if total_relink_failures:
        print(f"  Re-link FAILURES: {total_relink_failures} victims kept "
              f"(re-run to retry)")
    print(f"  Re-embedded: {total_embedded} templates")
    print(f"  Solo updated: {solo_updated}")

    http_client.close()
    conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-canonicalize and consolidate log_templates"
    )
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between embedding batches (default 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, no changes")
    parser.add_argument("--recount-only", action="store_true",
                        help="Only recompute event_count from log_events (safe with API running)")
    parser.add_argument("--allow-live-api", action="store_true",
                        help="Run even if the API is up (unsafe: stale template cache)")
    parser.add_argument("--embed-batch-size", type=int, default=20,
                        help="Embeddings per batch (default 20)")
    args = parser.parse_args(argv)

    if args.recount_only:
        recount_only()
        return 0

    print(f"Template reconsolidation (delay={args.delay}s, "
          f"dry_run={args.dry_run})")
    if not args.dry_run and not args.allow_live_api and _api_is_running():
        print(f"Refusing: the API answers at {API_HEALTH_URL}. Its in-memory "
              "TemplateCache maps old template hashes to ids this script deletes,\n"
              "so live ingest would link events to deleted templates.\n"
              "Stop it first (sudo systemctl stop devmesh-api), or pass "
              "--allow-live-api if you know it isn't ingesting.")
        return 2
    reconsolidate(args.delay, args.dry_run, args.embed_batch_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
