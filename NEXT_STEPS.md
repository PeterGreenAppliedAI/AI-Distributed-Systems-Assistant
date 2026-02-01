# DevMesh Platform - Next Steps

**Last Updated**: February 2026

---

## Current Status

### Phase 1: Logging Foundation — Complete
- [x] Real-time log collection from 7 nodes via journald shippers
- [x] 519K+ logs in MariaDB with deduplication
- [x] FastAPI ingestion and query API
- [x] Noise filtering, cursor-based recovery
- [x] Centralized error handling architecture
- [x] Automated deployment scripts

### Phase 1.5: Multi-Node Deployment — Mostly Complete

| Node | IP | Shipper | Logs |
|------|-----|---------|------|
| dev-services | 10.0.0.20 | Running | 382K+ |
| gpu-node | 10.0.0.19 | Running | 47K+ |
| gpu-node-3060 | 10.0.0.14 | Running | 40K+ |
| electrical-estimator | 10.0.0.13 | Running | 16K+ |
| teaching | 192.168.1.227 | Running | 14K+ |
| mariadb-vm | 10.0.0.18 | Running | 14K+ |
| postgres-vm | 192.168.1.220 | Running | 3K+ |
| monitoring-vm | 10.0.0.17 | **Pending** | — |

**Remaining**: Enable SSH on monitoring-vm and deploy shipper.

### Phase 2: Embeddings & Semantic Search — In Progress

- [x] `embedding_vector VECTOR(4096)` column added to `log_events`
- [x] Embedding service (`services/embedding.py`) with batch `/v1/embeddings` support
- [x] Live ingestion embeds new logs inline
- [x] Semantic search endpoint (`GET /search/logs`) with `VEC_DISTANCE_COSINE()`
- [x] Backfill script with ID-based cursor and thermal delay
- [x] Gateway config in `.env` (`GATEWAY_URL`, `EMBEDDING_MODEL`, `EMBEDDING_TIMEOUT`)
- [ ] Initial backfill of ~519K rows (in progress, ~55% done)
- [ ] HNSW vector index (after backfill completes, requires NOT NULL)
- [ ] Embedding versioning schema (HIGH — needed before canonicalization)
- [ ] Log canonicalization pipeline
- [ ] Template deduplication
- [ ] Cron safety net for missed embeddings

---

## Immediate TODO (Phase 2 Completion)

### 1. Finish Backfill
Backfill is running via `scripts/backfill_embeddings.py --batch-size 50 --delay 2`.
Once complete, create the HNSW index:
```bash
python3 db/migrations/002_add_embedding_vector.py --create-index
```

### 2. Cron Safety Net
Add cron job to catch logs where live embedding failed:
```
0 */6 * * * cd /home/tadeu718/devmesh-platform && python3 scripts/backfill_embeddings.py --batch-size 50 --delay 2 >> /var/log/devmesh-backfill.log 2>&1
```

### 3. Embedding Versioning Schema (HIGH PRIORITY — do this first)
Unversioned embeddings are the real cost. Without versioning, every pipeline change
(new model, new canonicalization rules, new chunking) means a painful full reprocess
with no way to A/B compare or incrementally migrate.

Add to the semantic layer table (or directly to `log_events`):
- `embedding_model` — exact model name (e.g. `qwen3-embedding:8b`)
- `embedding_dim` — vector dimensions (e.g. 4096)
- `canon_version` — canonicalization ruleset version (e.g. `v1`)
- `canon_hash` — hash of the canonicalized text (detect when text actually changed)
- `chunk_version` — chunking strategy version
- `created_at` — when this embedding was generated

This makes re-embedding routine:
- Only re-embed when `canon_hash` changes or model/version is bumped
- Old embeddings stay for comparison until explicitly dropped
- Backfill script can target specific versions: `WHERE canon_version < 'v2'`

**This must be in the schema before canonicalization work begins.**

### 4. Log Canonicalization (HIGH PRIORITY)
Raw systemd logs contain high-entropy tokens that destroy similarity clustering.
Build a canonicalization pipeline before re-embedding:
- `pid=1234` -> `pid=<PID>`
- IPs -> `<IPV4>` / `<IPV6>`
- Hex / hashes / UUIDs -> `<HEX>` / `<UUID>`
- User-specific paths -> `/home/<USER>/...`
- Strip timestamps embedded in message text
- Collapse whitespace

**Expected impact**: much better nearest-neighbor recall, fewer false uniques.

**Before building rules**: sample 200+ raw log lines, run canonicalizer, count uniques
before/after to measure actual compression ratio. Don't guess — measure.

### 5. Template Deduplication (HIGH PRIORITY)
519K logs likely compress to 50-150K unique message templates (measure this).
- Compute `template_hash = hash(canonical_message + service + level)`
- Only embed unique templates, store frequency + last_seen
- **Expected savings**: needs measurement, estimate 3-10x

### 6. Semantic Layer Table (after canonicalization proves out)
Separate table for deduplicated canonical text:
- `template_hash`, `canonical_text`, `embedding_vector`
- `embedding_model`, `embedding_dim`, `canon_version`, `canon_hash`, `chunk_version`
- `first_seen`, `last_seen`, `frequency`, `created_at`
- Raw `log_events` stays as audit/source-of-truth
- Search hits semantic layer, joins back to raw logs for context

---

## Backfill Performance Observations

- **Gateway throughput**: 50 texts in 2.4s via `/v1/embeddings` batch endpoint
- **Sequential `/api/embeddings`**: ~3.2s per text — 30x slower, avoid for bulk
- **DB bottleneck**: `WHERE embedding_vector IS NULL ORDER BY id` degrades to 17s full table scan as embedded rows increase; fixed with ID-based cursor tracking
- **Thermal behavior** (DGX Spark GB10):
  - No delay: 19 rows/s, GPU hit 80C
  - 2s inter-batch delay: 11 rows/s, GPU capped at ~70C
  - DGX Spark at 49C with low throughput = DB bottleneck, not thermal throttle

---

## Phase 3: Retrieval & LLM Reasoning

**Goal**: "Explain what happened during this incident"

- Vector + time-based retrieval pipeline
- LLM integration (Phi 4 for orchestration, larger model for synthesis)
- Context assembly: semantic search results + time window expansion
- Explanation API endpoint
- **Exit criteria**: Natural language explanations of log patterns

---

## Phase 4: Knowledge Graph & Agents

**Goal**: Multi-agent incident analysis with knowledge graph

- FalkorDB deployment
- Graph schema (`Service`, `Node`, `Incident`, `Error`)
- Graph projection ETL (logs -> graph)
- Multi-agent system (Planner, Log Analyst, Explainer, Runbook, Operator)
- GraphRAG: hybrid vector similarity + graph neighborhood retrieval
- Operator console (Streamlit or web UI)
- **Exit criteria**: Agents can correlate incidents across services

---

## Deployment Reference

### Deploy Shipper to a New Node
```bash
# Create package
cd /home/tadeu718/devmesh-platform
tar czf /tmp/devmesh-shipper.tar.gz \
    shipper/log_shipper_daemon.py \
    shipper/filter_config.py \
    shipper/filter_config.yaml \
    deploy/install_shipper.sh

# Copy and install on target
scp /tmp/devmesh-shipper.tar.gz user@NODE_IP:/tmp/
ssh user@NODE_IP "cd /tmp && tar xzf devmesh-shipper.tar.gz && sudo ./deploy/install_shipper.sh NODE_NAME API_HOST"
```

### Troubleshooting
```bash
# Shipper status
sudo systemctl status devmesh-shipper
sudo journalctl -u devmesh-shipper -n 50

# API health
curl http://10.0.0.20:8000/health

# Check embedding progress
python3 -c "from db.database import get_sync_connection; c=get_sync_connection(); cur=c.cursor(); cur.execute('SELECT COUNT(*) as done FROM log_events WHERE embedding_vector IS NOT NULL'); print(cur.fetchone()); c.close()"

# Test semantic search
curl "http://10.0.0.20:8000/search/logs?query=docker+restart&limit=5"
```
