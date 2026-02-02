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
- [x] Embedding versioning schema (in `log_templates` table: `embedding_model`, `embedding_dim`, `canon_version`, `canon_hash`, `chunk_version`)
- [x] Log canonicalization pipeline (`services/canonicalize.py` — v1 rules, 37 tests)
- [x] Template deduplication (`log_templates` table + template-aware ingest + `/search/templates` endpoint)
- [x] Cron safety net (`scripts/cron_template_safety_net.py`)
- [ ] Run migration 003 on production DB
- [ ] Run template backfill on existing ~540K rows
- [ ] Measure actual compression ratio (unique templates vs total rows)

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

### 3. ~~Embedding Versioning Schema~~ — DONE
Implemented in `log_templates` table with: `embedding_model`, `embedding_dim`,
`canon_version`, `canon_hash`, `chunk_version`, `created_at`, `updated_at`.

### 4. ~~Log Canonicalization~~ — DONE
Implemented in `services/canonicalize.py` with versioned v1 rules:
- UFW BLOCK fields, Loki structured logs, batch messages, PAM sessions, cron
- GIN/Ollama logs, DevMesh API timestamps, shipper PIDs
- Generic: ISO timestamps, UUIDs, hex strings, IPs, MACs, PIDs, durations, large numbers
- Whitespace collapse
- 37 tests in `tests/test_canonicalize.py`

### 5. ~~Template Deduplication~~ — DONE
Implemented as `log_templates` table (migration 003) + template-aware ingest:
- `log_templates` stores unique canonical templates with HNSW-indexed embeddings
- `log_events.template_id` links raw logs to templates (nullable, no FK)
- Ingest canonicalizes → hash lookup (cache → DB) → embed only new templates
- `GET /search/templates` — two-step search: vector on templates, then examples from log_events
- Template cache warmed at startup, per-worker LRU with 100K max entries

### 6. Deploy Canonicalization + Templates
```bash
# Run migration
python3 db/migrations/003_create_log_templates.py

# Backfill existing rows
python3 scripts/backfill_templates.py --batch-size 50 --delay 2

# Add cron safety net
0 */6 * * * cd /home/tadeu718/devmesh-platform && python3 scripts/cron_template_safety_net.py --batch-size 100 --delay 2 >> /var/log/devmesh-template-safety.log 2>&1
```

### 7. Measure Compression Ratio
After backfill, check:
```sql
SELECT COUNT(*) FROM log_templates;  -- unique templates
SELECT COUNT(*) FROM log_events;     -- total raw logs
-- Expected: 540K raw → 5K-50K templates (10-100x compression)
```

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
