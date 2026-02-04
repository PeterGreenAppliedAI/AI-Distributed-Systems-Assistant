# DevMesh Platform - Next Steps

**Last Updated**: February 2026

---

## Current Status

### Phase 1: Logging Foundation — Complete
- [x] Real-time log collection from 7 nodes via journald shippers
- [x] 928K+ logs in MariaDB with deduplication
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

### Phase 2: Embeddings & Semantic Search — Complete

- [x] `embedding_vector VECTOR(4096)` column added to `log_events`
- [x] Embedding service (`services/embedding.py`) with batch `/v1/embeddings` support
- [x] Live ingestion embeds new logs inline
- [x] Semantic search endpoint (`GET /search/logs`) with `VEC_DISTANCE_COSINE()`
- [x] Backfill script with ID-based cursor and thermal delay
- [x] Gateway config in `.env` (`GATEWAY_URL`, `EMBEDDING_MODEL`, `EMBEDDING_TIMEOUT`)
- [x] Embedding versioning schema (in `log_templates` table: `embedding_model`, `embedding_dim`, `canon_version`, `canon_hash`, `chunk_version`)
- [x] Log canonicalization pipeline (`services/canonicalize.py` — v1 rules, 37 tests)
- [x] Template deduplication (`log_templates` table + template-aware ingest + `/search/templates` endpoint)
- [x] Cron safety net (`scripts/cron_template_safety_net.py`)
- [x] Template TTL cleanup (stale templates pruned alongside log_events)
- [x] Compression ratio measured: **928K logs → 5,944 templates (151x compression)**

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

### 7. ~~Measure Compression Ratio~~ — DONE
```sql
SELECT COUNT(*) FROM log_templates;  -- 5,944 unique templates
SELECT COUNT(*) FROM log_events;     -- 928K raw logs
-- Result: 151x compression (exceeded 10-100x expectation)
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

## Phase 3: LLM Reasoning Layer — In Progress

**Goal**: Natural language log analysis with LLM tool calling

- [x] LLM config in `.env` (`LLM_MODEL`, `LLM_TIMEOUT`, `LLM_MAX_ITERATIONS`)
- [x] LLM error types (`LLMError`, `LLMTimeoutError`, `LLMToolError`)
- [x] Request/response models (`AnalyzeRequest`, `AnalyzeResponse`, `ToolCallRecord`)
- [x] 5 LLM tools (`search_templates`, `query_logs`, `get_template_stats`, `get_service_overview`, `get_log_context`)
- [x] LLM orchestration loop (`services/llm.py`) with Ollama `/api/chat` tool calling
- [x] `POST /analyze` endpoint wired up in `api/routes.py`
- [x] 26 new tests (114 total, all passing)
- [x] End-to-end validation with live Ollama + `nemotron-3-nano:30b`
- [x] Frontend UI (`/ui` → `static/index.html`) — dark theme, filters, markdown rendering, query history
- [x] Two-phase synthesis — separate tool-calling loop from analysis generation
- [x] Fallback parser for hallucinated tool names (alias map + text JSON recovery)
- [x] Tune system prompt based on real query results
- **Exit criteria**: Natural language explanations of log patterns with cited evidence — **achieved**

#### Observations from live testing
- Nemotron-3-nano struggles with Ollama's structured `tool_calls` format — frequently outputs tool calls as text JSON or invents tool names (`RunLogSearch`, `search_log_templates`)
- The same model works well with text-based ReAct (Thought/Action/Observation) in other projects (Discord bot with multi-hop search) — the issue is Ollama's tool-call format, not the model's reasoning ability
- Two-phase synthesis (tool loop → separate summarization call) dramatically improved output quality
- The model does its best work in the first 2-3 tool calls; beyond that it starts hallucinating
- Service overview reports 24h errors; raw query_logs returns all-time — the model caught this inconsistency

#### Possible next direction: deterministic routing + ReAct
- **Deterministic tool routing**: Python classifies the query and picks tools (like the Discord bot pattern — check known data first, fill gaps second), LLM only does synthesis
- **ReAct over Ollama tool_calls**: If the model needs to drive tool selection, use text-based Thought/Action/Observation parsing instead of Ollama's structured format
- These are not mutually exclusive — route obvious queries deterministically, fall back to ReAct for ambiguous ones

---

## Phase 4: Infrastructure Memory Platform

**Reframe**: The logs are raw material, not the product. The value is a system that builds and maintains structured memory of what the infrastructure does — and reasons over that memory.

**What "memory" means here**:
- Templates are compressed memories (928K events → 5,944 patterns, 151x compression)
- Temporal awareness: knowing what "normal" looks like per host/service, detecting drift
- Correlation: connecting an OOM kill on Jan 20 to behavior changes after
- Pattern lifecycle: tracking when templates first appear, spike, or disappear

### Proactive Detection Architecture

The goal: shift from reactive (human asks question) to proactive (system alerts on anomalies).

**Key insight**: The "always-on watchful eye" doesn't need to be an LLM. Cheap detector → expensive investigation.

```
┌─────────────────────────────────────────────────────────┐
│  Log Stream (continuous ingestion)                      │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Detector Layer (cheap, always-on, Python + SQL)        │
│  - Error rate spike in time window                      │
│  - New ERROR template never seen before                 │
│  - Template frequency anomaly (2σ from baseline)        │
│  - Service went silent (expected logs missing)          │
│  - Correlation triggers (OOM + service restart)         │
└─────────────────────────────────────────────────────────┘
                          │
                    (threshold crossed)
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  CrewAI Investigation (expensive, on-demand)            │
│  ├── Timeline Agent: reconstruct last 30 minutes       │
│  ├── Root Cause Agent: correlate errors, find origin   │
│  ├── Impact Agent: what else was affected              │
│  └── Reporter Agent: structured incident summary       │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
              Notification (Discord / Email / Dashboard)
```

**Why CrewAI makes sense here** (vs the current two-phase approach):
- Investigation is genuinely multi-step with different tools per phase
- Each agent has focused context and narrow prompts
- Delegation logic: "if error count > 100, add triage agent"
- The detector layer keeps LLM costs near zero until something's actually wrong

### Graph-Enhanced Diagnosis (FalkorDB)

The graph turns isolated facts into diagnosable relationships:

**Without graph**:
```
Alert: mariadb.service had 50 errors
Alert: devmesh-api.service restarted 3 times
Alert: ollama.service timeouts increased
(Three separate facts — human connects the dots)
```

**With graph**:
```
mariadb.service ──PROVIDES_DB_FOR──→ devmesh-api.service
devmesh-api.service ──CALLS──→ ollama.service
dev-services ──RUNS──→ [mariadb, devmesh-api]

Investigation agent traverses:
  "mariadb errors"
    → what depends on mariadb? → devmesh-api
    → what does devmesh-api call? → ollama
    → conclusion: mariadb errors cascaded through API to ollama timeouts
```

**What the graph enables per agent**:

| Agent | Without Graph | With Graph |
|-------|---------------|------------|
| Timeline | "these errors happened" | "these errors happened, here's what they touched" |
| Root Cause | "first error was in X" | "first error in X, which is upstream of Y and Z" |
| Impact | "these services also errored" | "these are downstream, expected to be affected" |
| Reporter | list of facts | narrative with causality |

The graph is the difference between **correlation** (these happened together) and **causation** (this caused that because of this relationship).

### Implementation Plan

#### Phase 4A: Incident Pipeline (before graph)

**New tables:**
```sql
-- Detector state (one row per detector type per service)
detector_state (
    detector_name,      -- 'error_spike', 'silence', 'new_template'
    service,            -- nullable, some detectors are global
    last_run_ts,
    ewma_value,         -- exponentially weighted moving average
    stddev,             -- for threshold calculation
    sample_count        -- how many windows observed
)

-- Incidents (one row per triggered event)
incidents (
    id,
    trigger_type,       -- which detector fired
    severity,           -- critical, warning, info
    service,
    host,
    created_ts,
    status,             -- open, investigating, closed
    context_bundle,     -- JSON blob from context packer
    cooldown_until      -- prevent re-triggering
)

-- Investigation results
incident_reports (
    id,
    incident_id,        -- FK to incidents
    created_ts,
    report_md,          -- markdown summary
    root_cause_hypothesis,
    confidence,         -- how sure is the model
    evidence_template_ids,  -- which templates support this
    suggested_actions   -- what to do next
)
```

**Components to build:**
- [ ] Migration for incident pipeline tables
- [ ] `infra/detector.py` — runs every 60s, checks thresholds, writes to `incidents`
- [ ] Detector types (start with these):
  - Error-rate spike per service (EWMA baseline)
  - New ERROR template (first_seen within window)
  - Silence detector (expected logs missing)
  - Catastrophic primitives (OOM, disk full, connection refused templates)
- [ ] Context packer function — bundle relevant templates for investigation
- [ ] CrewAI crew definition (Timeline, Root Cause, Impact, Reporter agents)
- [ ] Notification integration (Discord webhook first)
- [ ] Learning period logic (observe-only until baseline established)
- [ ] Cooldown/dedup logic (same incident doesn't re-trigger within window)

**Exit criteria**: System detects real anomalies, runs investigation, posts report to Discord.

#### Phase 4B: Minimal Graph (after incident pipeline works)

Don't infer the graph — own it manually first.

**Start with `inventory.yaml`:**
```yaml
services:
  mariadb:
    hosts: [mariadb-vm]
    type: database
  devmesh-api:
    hosts: [dev-services]
    depends_on: [mariadb, ollama]
    type: api
  ollama:
    hosts: [gpu-node]
    type: inference
  devmesh-shipper:
    hosts: [all]
    type: agent

edges:
  - from: devmesh-api
    to: mariadb
    type: DEPENDS_ON
  - from: devmesh-api
    to: ollama
    type: CALLS
```

**Then:**
- [ ] Deploy FalkorDB
- [ ] Graph schema (`Service`, `Host`, `Database` nodes; `DEPENDS_ON`, `CALLS`, `RUNS_ON` edges)
- [ ] Loader script: `inventory.yaml` → FalkorDB
- [ ] Update Impact Agent to use graph traversal for blast radius

#### Phase 4C: Graph Enrichment (after manual graph is trusted)

Learn edges from observations:
- Network connections
- Config file parsing
- Log template analysis ("connected to X", "querying Y", "failed to reach Z")

Only do this after the manual truth graph exists and the incident pipeline is battle-tested.

**Exit criteria**: System proactively alerts on anomalies, provides root cause analysis with causal narrative from graph traversal, without human prompting

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
