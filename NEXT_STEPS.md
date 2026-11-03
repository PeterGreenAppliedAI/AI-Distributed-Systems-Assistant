# DevMesh Platform - Next Steps

**Last Updated**: 2026-11-03

---

## ⏸ PAUSED (2026-11-03)

Platform intentionally paused while MariaDB (10.0.0.18) is down. Done
2026-11-03 with `systemctl disable --now`:
- dev-services: devmesh-shipper, devmesh-api, devmesh-healthcheck.timer, devmesh-ttl-cleanup.timer
- shippers on gpu-node, gpu-node-3060, mariadb-vm, electrical-estimator, postgres-vm
- teaching (192.168.1.227): not reached; node is offline. Its shipper is still
  enabled and will start spooling when the node comes back. Disable it then. To resume: restore the DB,
then re-enable services, then drain the spool (see 2026-11-03 outage section).

## Future Ideas (not scheduled — after Phase 3/4)

- **Fleet-wide scraping**: beyond journald, e.g. metrics, service inventory,
  config/package changes, so the platform knows *when things happened* across all nodes
- **Event timeline / change detection**: "what changed on which node, when"
- **Dashboard** reachable over VPN into the home network
- **Phone monitoring**: mobile-friendly dashboard or app + push notifications
  (pairs with the healthcheck notification TODO — ntfy is a cheap first step)

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

## 2026-11-03 — ACTIVE OUTAGE: MariaDB unreachable

Found while addressing the external review. **10.0.0.18:3306 refuses connections**
(SSH on :22 is up). Healthcheck has been `crit` the whole time, but alerts only
reach syslog, so nobody saw them.
- Every TTL run in the journal (since 2026-10-08) failed with error 2003.
- `shipper/failed_batches.jsonl` on dev-services is **4.8 GB**; oldest entry
  2026-05-10, so the outage may date back to just after the May incident.
- Same symptom as the May incident. Suspected repeat disk-full / HNSW bloat,
  **unconfirmed**. Check `df -h` and `ls -lh /var/lib/mysql/<db>/` on the VM.
- [ ] Restore MariaDB (stop API before any `log_templates` DDL — see runbook)
- [ ] Drain spool: `python3 scripts/drain_failed_batches.py` (now streams; the
      old version would have loaded all 4.8 GB into RAM)
- [ ] Wire healthcheck to a real notification channel. **This outage is why it matters.**

## 2026-11-03 — External Review Fixes

Fixed (tests in `tests/test_review_fixes.py`, 12 new, 126 total):
- [x] Stored XSS: UI markdown now `DOMPurify.sanitize(marked.parse(...))`; history escaped
- [x] UI JS vendored + pinned in `static/vendor/` (marked 15.0.12, DOMPurify 3.2.6), so no CDN
- [x] Auth + UI: `/ui` and `/static/*` public; UI prompts for and sends `X-API-Key`;
      drain script sends `API_KEY`; constant-time key compare
- [x] Template `event_count` double-count on create and inflation on duplicate resend.
      New templates start at 0 and are counted post-insert only for rows actually inserted
- [x] Failed schema checks no longer cached; DB pool retries in background if down at boot
- [x] Migration 002 `--create-index` refuses when `log_templates` exists
      (NOT NULL `log_events.embedding_vector` would break template-mode ingest)
- [x] LLM tool args clamped (limit ≤200, hours ≤720, window ≤120 min)
- [x] `truncate_result` returns valid JSON (whole items + marker), so sources survive truncation
- [x] `search_templates` tool param-order bug (filters bound to the wrong placeholders)
- [x] Service-overview rows no longer reported as "cited events"
- [x] `load_dotenv()` runs before app imports
- [x] Healthcheck staleness computed DB-side vs `UTC_TIMESTAMP()`
- [x] `reconsolidate_templates.py`: keeps victims whose re-link failed; survivor takes
      MIN(first_seen)/MAX(last_seen)/merged hosts; refuses while API is up
- [x] `drain_failed_batches.py`: streams, renames spool before reading, saves bad lines
      to `.rejected`, correct completion logic, never clobbers an old `.drained`
- [x] Deploy package ships `shipper/transforms.py`
- [x] Runbook: `/search/templates?query=&limit=`, `ADD VECTOR INDEX` (documented form)
- [x] README/PROJECT_SUMMARY: FK claim, TTL wording, test/line counts, stale-count notes

Follow-up review (same day), fixed, 133 tests total:
- [x] HTTP `/search/templates` + `/search/logs` had the same placeholder-order bug
      as the LLM tool (filtered search bound the filter to `VEC_FromText`). Fixed + tested
- [x] `/search/logs` marked deprecated (template mode leaves new rows' vectors NULL)
- [x] Schema checks fail closed (503) instead of silently using the legacy ingest path
- [x] Shutdown awaits the cancelled pool-retry task (no leaked pool)
- [x] Manual tar deploy docs (README, below) include `shipper/transforms.py`
- [x] `.env.example` has LLM settings + UI API-key note
- [x] `reconsolidate_templates.py --recount-only`: heals event_count drift, safe with API up
- [x] Tests for drain script, healthcheck staleness, HTTP search param order

Known, accepted: template counters can drift. Two concurrent ingests of the same
new log can both count it (the pre-check runs before INSERT IGNORE), and a failed
lookup or update undercounts. Heal with `--recount-only`.

Still open:
- [ ] Re-measure events/templates/compression once DB is back; replace stale doc numbers
- [ ] Embedding calls unchunked; sequential fallback still 120 s/text timeout
- [ ] `infra/docker-compose.yml` is legacy (points at `backend/Dockerfile`, file is
      `DockerFile`, runs the old stub). Fix or delete; prod runs via systemd
- [ ] Auth still off by default; plaintext HTTP
- [ ] Ingest tests mock SQL only; no test against a real MariaDB

## 2026-05-06 Incident & Platform Self-Monitoring

**What happened**: MariaDB VM `/` filled to 100%, ingest failed silently for
7 days. Root cause was a 49 GB HNSW vector index file
(`log_templates#i#05.ibd`) — invisible to `information_schema.tables.index_length`
because MariaDB vector indexes don't report there. Disk pressure was masked
until MariaDB couldn't autoextend further.

**Resolution**:
- Resized VM disk 100 GB → 250 GB (`growpart` + `resize2fs`).
- Rebuilt vector index: `ALTER ... DROP KEY` then `ADD VECTOR KEY`. With API
  stopped, completes in **~45 seconds** for 23k rows × 4096-dim. With API
  running, the rebuild is pathological (>2.6 hr, must kill) — see runbook
  `scripts/rebuild_template_vector_index.md`.
- TTL was never enforcing 90 days because the systemd timer was never
  installed. Installed + ran — deleted 970,726 events older than 90 days.
- Drained `shipper/failed_batches.jsonl` (488 MB / ~24k batches) back through
  the API.

### Platform Self-Monitoring — NEW
- [x] `scripts/healthcheck.py` — disk, services, DB connect, ingest staleness,
      shipper backlog. Outputs JSON status + syslog warnings.
- [x] `infra/devmesh-healthcheck.{service,timer}` — runs every 5 minutes.
- [x] **Install on dev-services** (verified active 2026-11-03):
      ```bash
      sudo cp /home/tadeu718/devmesh-platform/infra/devmesh-healthcheck.{service,timer} \
              /etc/systemd/system/
      sudo systemctl daemon-reload
      sudo systemctl enable --now devmesh-healthcheck.timer
      ```
- [x] **Install TTL timer** (verified active 2026-11-03 — but every run since at least 2026-10-08 failed: DB unreachable):
      ```bash
      sudo cp /home/tadeu718/devmesh-platform/infra/devmesh-ttl-cleanup.{service,timer} \
              /etc/systemd/system/
      sudo systemctl daemon-reload
      sudo systemctl enable --now devmesh-ttl-cleanup.timer
      ```
- [ ] Wire `/tmp/devmesh-health.json` (or syslog `devmesh-health` tag) to a
      real notification channel (ntfy/discord/email). Currently alerts only
      reach syslog and the platform's own ingest pipeline.
- [ ] Add filesystem-level `.ibd` size diff to healthcheck so HNSW bloat
      surfaces before it eats the disk. (Vector indexes don't show up in
      `information_schema.tables.index_length`.)
- [ ] Optional: `OPTIMIZE TABLE log_events` to reclaim ~13 GB of fragmented
      space from the TTL purge. Cosmetic — InnoDB will reuse the holes
      naturally as new events ingest. Schedule with API stopped if doing it.

### Lessons Captured
- HNSW vector index rebuilds (and any DDL on `log_templates`) require the API
  to be stopped — UPDATE-heavy workload otherwise stalls the merge phase
  indefinitely. See memory `hnsw_rebuild_needs_quiet_window`.
- Vector index sizes don't surface through `information_schema`. Filesystem
  inspection (`ls -lh /var/lib/mysql/<db>/`) is the only ground truth. See
  memory `vector_index_invisible_to_standard_size_stats`.
- TTL `delete_stale_templates` collides with concurrent ingest UPDATEs
  (MariaDB error 1020). Patched to soft-fail and continue — log_events
  cleanup is the bulk-bytes job and runs cleanly.
- The shipper's local `failed_batches.jsonl` buffering worked exactly as
  designed during the 7-day outage. No events were lost.

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
- [x] 26 new tests (code + tests landed in fea727c, 2026-11-03; 133 total after review fixes)
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
    shipper/transforms.py \
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
