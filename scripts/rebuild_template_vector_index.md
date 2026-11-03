# Runbook: Rebuild `log_templates` HNSW Vector Index

## Why

`log_templates#i#05.ibd` (the HNSW vector index on `embedding_vector`) grew to
49 GB while the table itself is 764 MB. Caused the 2026-05-06 disk-full
incident on the mariadb VM. Root cause: aggressive InnoDB tablespace
autoextend during heavy template churn — `AUTO_INCREMENT=108,069` with only
22,595 live rows means ~85k templates were inserted and superseded over the
table's lifetime, leaving the HNSW graph file with mostly dead extents.

This runbook reclaims that space by dropping and rebuilding the vector index.

## Pre-flight facts (snapshot 2026-05-06)

| Thing | Value |
|---|---|
| Vector dim | 4096 (qwen3-embedding:8b) |
| Live rows | 22,595 |
| AUTO_INCREMENT | 108,069 (~85k churn) |
| `log_templates.ibd` (data) | 764 MB |
| `log_templates#i#05.ibd` (vector idx) | **49 GB** |
| Expected post-rebuild idx size | ~0.5–2 GB |
| Estimated reclaim | ~47 GB |
| Free disk on /var/lib/mysql | 140 GB ✓ |
| Ingest rate | ~800k events/hr, flat 24/7 — no off-peak window |

## Impact during rebuild

**API MUST be stopped first. Verified the hard way 2026-05-06.**

- **With API running** (DO NOT DO THIS): the ALTER's build phase takes ~30+
  min during which `event_count` UPDATE traffic blocks on metadata lock.
  Once the build finishes, the merge phase (`Update_rows_log_event::*`)
  drains accumulated row events at a rate slower than HNSW maintenance
  costs allow. Observed 2.6 hours with no completion; killed.
- **With API stopped**: ALTER completes in ~45 seconds for 23k rows × 4096
  dims. Clean. The shippers across the fleet buffer to their local
  `failed_batches.jsonl` and resume on API restart — proven recovery.
- **Vector search: offline** for the duration regardless. `/search/templates`
  returns degraded results from the moment the DROP commits until the new
  index is live.

## Approach: DROP + ADD VECTOR INDEX (preferred over `OPTIMIZE TABLE`)

Smaller blast radius, faster, only touches the broken thing. Full
`OPTIMIZE TABLE` would also work but rebuilds every index and locks the
table for ~30 min — overkill.

## Steps

### 0. Stop the API on dev-services (REQUIRED)

```bash
sudo systemctl stop devmesh-api.service
```

Wait ~5 seconds. Confirm `SHOW FULL PROCESSLIST` on the DB has no busy
queries before proceeding. Each shipper's `failed_batches.jsonl` will
grow during the window — that's expected and recoverable on API restart.

### 1. Pre-check (read-only, safe to run anytime)

```sql
-- Confirm current state
SELECT COUNT(*) AS rows FROM log_templates;
SHOW INDEX FROM log_templates WHERE Key_name = 'idx_template_embedding';
```

```bash
# On the mariadb VM
sudo ls -lh /var/lib/mysql/devmesh/log_templates*.ibd
df -h /var/lib/mysql
```

Record the index file size and free space — you'll compare after.

### 2. Drop the vector index

```sql
ALTER TABLE log_templates DROP KEY idx_template_embedding;
```

This is fast — InnoDB just unlinks the `#i#05.ibd` file. Free space jumps
by ~49 GB immediately. From this point, vector search is offline.

### 3. Rebuild the vector index

```sql
ALTER TABLE log_templates
  ADD VECTOR INDEX idx_template_embedding (embedding_vector);
```

Has to insert ~23k 4096-dim vectors into a fresh HNSW graph. **With API
stopped: ~45 seconds.** The session will block until it returns; do not
Ctrl-C mid-build (leaves the table without a vector index — recoverable
but messy).

Run from a screen/tmux session or a long-lived `mysql` client. If you want
a progress signal, in another shell:

```bash
watch -n 5 'sudo ls -lh /var/lib/mysql/devmesh/log_templates*.ibd'
```

You'll see the new `#i#XX.ibd` file grow as the graph builds.

### 4. Verify

```sql
SHOW INDEX FROM log_templates WHERE Key_name = 'idx_template_embedding';
-- Index_type should be VECTOR

-- Sanity check vector search still returns results
SELECT id, service, canonical_text
FROM log_templates
ORDER BY VEC_DISTANCE(embedding_vector,
                      (SELECT embedding_vector FROM log_templates WHERE id = 4))
LIMIT 5;
```

```bash
sudo ls -lh /var/lib/mysql/devmesh/log_templates*.ibd
df -h /var/lib/mysql
```

Expected: new index file is sub-2 GB, free space is ~47 GB higher than
before, search query returns 5 rows with the UFW BLOCK template (id=4) at
top.

### 5. Restart API

```bash
sudo systemctl start devmesh-api.service
```

Each shipper auto-recovers within its retry interval. Live ingest resumes
within seconds.

### 6. End-to-end test

```bash
curl -s 'http://localhost:8000/search/templates?query=ufw+block&limit=5' | head
```

## Rollback

There's no rollback for the DROP — the old bloated file is gone the moment
you drop the index. The "rollback" path is just: re-run step 3 if step 3
itself failed midway. The data in `embedding_vector` is the source of truth;
the index is derived from it and can always be rebuilt.

If step 3 errors out:
- Confirm with `SHOW INDEX FROM log_templates` that no vector index exists.
- Re-run the `ADD VECTOR INDEX` statement.
- If it persistently fails, capture `journalctl -u mariadb -n 100 -l` and
  investigate before retrying.

## Post-rebuild

1. Update `NEXT_STEPS.md`:
   - Mark "rebuild log_templates vector index" complete.
   - Add observation: "HNSW index file size invisible to
     `information_schema.tables.index_length` — needs filesystem-level
     monitoring."
2. Add a periodic check to flag this class of bloat going forward. See
   the platform self-monitoring tier in `NEXT_STEPS.md`.
3. The 49 GB → ~1 GB delta is one-time cleanup. Ongoing growth from
   normal template churn is slow; revisit only if the file exceeds
   ~5× the data file size again.

## Why not also reduce TTL?

Considered and rejected during the incident. TTL operates on `log_events`
(35 GB / 29M rows). Dropping 90 → 30 days reclaims ~13 GB of events,
versus ~47 GB from this index rebuild. The index was the real disk
pressure, not event volume. Retention policy can be revisited
separately as a product decision, not as disk pressure response.
