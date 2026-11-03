# DevMesh Platform — Project Summary

## One-liner

**AI-native observability platform that turns raw infrastructure logs into structured memory, with natural language querying — fully local, zero API costs.**

---

## The Problem

- 7 Linux nodes generating continuous log streams across 137 services
- Traditional log aggregation (Grafana/Loki, Datadog, ELK) gives you search, not understanding
- Operators still manually grep through logs to diagnose issues
- No system "remembers" what normal looks like or detects drift over time

## The Solution

A local-first platform that:
1. **Collects** logs in real-time from all nodes into a single store
2. **Compresses** 899K raw events into 5,713 canonical templates (157x compression)
3. **Embeds** templates as vectors for semantic search (not just keyword matching)
4. **Reasons** over the data using a local LLM that can call tools, investigate, and synthesize answers
5. **Presents** results through a browser-based UI with natural language input

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        7 Linux Nodes                                 │
│  dev-services · gpu-node · gpu-node-3060 · electrical-estimator      │
│  teaching · mariadb-vm · postgres-vm                                 │
└──────────────┬───────────────────────────────────────────────────────┘
               │ journald → Log Shipper Daemon (per node)
               │ Real-time batching (50/batch), noise filtering
               │ Cursor-based recovery on restart
               ↓
┌──────────────────────────┐      ┌────────────────────────────┐
│  DevMesh API (FastAPI)   │─────→│  LLM Gateway (Ollama)      │
│  10.0.0.20:8000          │      │  192.168.1.184:8001        │
│                          │←─────│  DGX Spark GB10            │
│  Endpoints:              │      │                            │
│  · POST /ingest/logs     │      │  Models:                   │
│  · GET  /query/logs      │      │  · qwen3-embedding:8b      │
│  · GET  /search/logs     │      │    (4096-dim vectors)      │
│  · GET  /search/templates│      │  · nemotron-3-nano:30b     │
│  · POST /analyze         │      │    (reasoning + tool use)  │
│  · GET  /ui              │      │                            │
└──────────────┬───────────┘      └────────────────────────────┘
               │
               ↓
┌──────────────────────────┐
│  MariaDB 12.0.2          │
│  10.0.0.18               │
│                          │
│  Tables:                 │
│  · log_events (899K rows)│
│    - VECTOR(4096) column │
│    - template_id FK      │
│  · log_templates (5,713) │
│    - HNSW vector index   │
│    - canonical_text      │
│    - event_count         │
└──────────────────────────┘
```

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Nodes monitored | 7 |
| Total log events | 898,959 |
| Unique services | 137 |
| Canonical templates | 5,713 |
| Compression ratio | **157x** (899K → 5.7K patterns) |
| Embedding dimensions | 4,096 |
| Test suite | 114 tests, all passing |
| Codebase | 42 Python files, ~4,400 lines |
| External API costs | **$0** (all inference local) |

---

## Log Level Distribution

| Level | Count | Percentage |
|-------|-------|------------|
| INFO | 804,072 | 89.5% |
| WARN | 94,106 | 10.5% |
| DEBUG | 766 | < 0.1% |
| ERROR | 8 | < 0.1% |
| CRITICAL | 7 | < 0.1% |

---

## How It Works — The Pipeline

### 1. Collection (Phase 1 — Complete)
- Custom Python daemon on each node tails `journald` in real-time
- Batches 50 logs per request to the API
- SHA256-based deduplication prevents duplicates
- Cursor-based recovery — no lost logs on shipper restart
- Noise filtering via configurable YAML rules

### 2. Canonicalization & Templates (Phase 2 — Complete)
- Raw log messages are canonicalized: IPs, PIDs, timestamps, UUIDs, hex strings → placeholders
- Example: `[UFW BLOCK] IN=ens18 SRC=192.168.1.1 DST=224.0.0.1 LEN=36 ID=41056`
  becomes: `[UFW BLOCK] IN=ens18 SRC=<IPV4> DST=<IPV4> LEN=<N> ID=<N>`
- Canonical text is hashed → deduplicated into `log_templates` table
- 899K raw events compress to 5,713 unique patterns
- In-memory template cache (LRU, 100K max) for fast ingest-time lookups

### 3. Embedding & Semantic Search (Phase 2 — Complete)
- Templates embedded via `qwen3-embedding:8b` (4096 dimensions)
- MariaDB native `VECTOR` type with `VEC_DISTANCE_COSINE()` search
- Semantic search: "authentication failures" finds PAM, SSH, sudo patterns
- Two-step search: vector match on templates → retrieve example events

### 4. LLM Reasoning (Phase 3 — Complete)
- `POST /analyze` accepts natural language questions
- **Deterministic scaffolding**: service overview and scoped logs pre-fetched before LLM starts
- **Dynamic tool loop**: LLM calls 5 tools to investigate (up to 5 iterations)
- **Synthesis fallback**: if the LLM's answer is weak, a second pass synthesizes from collected data
- **Deflection detection**: catches "let me know what you'd like" non-answers and forces synthesis

### 5. Frontend (Phase 3 — Complete)
- Single HTML file, dark theme, no build tools
- Natural language query input with optional filters (host, service, time range)
- Markdown-rendered explanations with collapsible tool calls and sources
- Query history in localStorage

---

## The 5 LLM Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `get_service_overview` | High-level summary: event counts, errors, active hosts per service | "Which services are noisiest?" |
| `search_templates` | Semantic vector search on canonical log patterns | "Find authentication-related patterns" |
| `query_logs` | Filtered raw log retrieval (host, service, level, time) | "Show ERROR events on gpu-node" |
| `get_template_stats` | Template frequency over a time window — anomaly detection | "What patterns spiked in the last hour?" |
| `get_log_context` | Surrounding events for a specific log entry | "What happened before event #376640?" |

---

## Live Query Examples

**"What services have the most errors?"** (2 iterations)
> Only session-4862.scope reports errors (2 events). All other services report 0. The errors are `pam_unix(sudo:auth): conversation failed` on dev-services.

**"What patterns appear most frequently in the last hour?"** (2 iterations)
> Top patterns: GIN HTTP handler (200 hits), Loki table manager (57), UFW BLOCK on two interfaces (55 + 45).

**"Are any services behaving differently than usual?"** (4 iterations)
> session-4862.scope is the only anomaly — 2 authentication errors while every other service is clean. Found the specific timestamps and PAM failure messages.

**"Show me the ERROR and CRITICAL events"** (5 iterations)
> 8 ERROR events found: 7x pam_unix(sudo:auth) conversation failed across multiple sessions (Nov 2025 – Feb 2026), 1x kernel OOM kill of next-server using 4.7GB RSS (Jan 20). No CRITICAL events returned.

---

## Orchestration — Three-Phase Architecture

```
┌─────────────────────────────────────────────────────┐
│  Phase 1: Deterministic Scaffolding (Python)         │
│  · Always pre-fetch get_service_overview(24h)        │
│  · If filters set, pre-fetch query_logs(scoped)      │
│  · ~200ms DB time, gives LLM full context upfront    │
├─────────────────────────────────────────────────────┤
│  Phase 2: Dynamic LLM Loop (up to 5 iterations)     │
│  · Model reasons over pre-fetched data               │
│  · Calls additional tools as needed                  │
│  · Candidate answer tracking (saves best mid-loop)   │
│  · Fallback parser for hallucinated tool names       │
├─────────────────────────────────────────────────────┤
│  Phase 3: Synthesis Safety Net                       │
│  · Detects deflections ("let me know...")            │
│  · Uses saved candidate answer if available          │
│  · Falls back to dedicated synthesis LLM call        │
│  · Strict prompt: "answer the question, no filler"   │
└─────────────────────────────────────────────────────┘
```

---

## Hardware

| Component | Role | Specs |
|-----------|------|-------|
| dev-services (10.0.0.20) | API server, primary node | Linux host |
| DGX Spark GB10 (192.168.1.184) | LLM Gateway (Ollama) | NVIDIA Grace Blackwell, manages thermals at 70C with 2s batch delay |
| MariaDB VM (10.0.0.18) | Database | MariaDB 12.0.2 with native VECTOR type |
| gpu-node (10.0.0.19) | Compute node | GPU-equipped |
| gpu-node-3060 (10.0.0.14) | Compute node | RTX 3060 |
| + 3 more nodes | Various workloads | electrical-estimator, teaching, postgres-vm |

**All inference is local. Zero external API calls. Zero cloud costs.**

---

## Observations & Lessons Learned

### Technical
- MariaDB VECTOR columns need `VEC_FromText(%s)` — raw assignment fails
- Ollama `/v1/embeddings` batch endpoint is **30x faster** than sequential `/api/embeddings`
- `WHERE column IS NULL ORDER BY id` degrades to full table scan — use ID-based cursor tracking
- **Canonicalize before embedding** — raw logs with PIDs/IPs destroy similarity clustering
- DGX Spark thermals: 19 rows/s → 80C; 2s inter-batch delay caps at 70C

### LLM Behavior (nemotron-3-nano:30b)
- Struggles with Ollama's structured `tool_calls` format — frequently outputs tool calls as text JSON
- Same model works well with text-based ReAct (Thought/Action/Observation) in other projects
- The issue is Ollama's tool-call format, not the model's reasoning ability
- Two-phase synthesis dramatically improved output quality
- Best work happens in first 2-3 tool calls; degrades with more iterations
- Deterministic pre-fetching cut typical queries from 8 iterations to 2

---

## What's Next — Phase 4: Infrastructure Memory Platform

The logs are raw material, not the product. The value is a system that builds and maintains **structured memory** of what the infrastructure does — and reasons over that memory.

### What "Memory" Means
- Templates are **compressed memories** (899K events → 5,713 patterns)
- **Temporal awareness**: knowing what "normal" looks like per host/service, detecting drift
- **Correlation**: connecting an OOM kill on Jan 20 to behavior changes after
- **Pattern lifecycle**: tracking when templates first appear, spike, or disappear

### Planned Components
- **FalkorDB knowledge graph** — Service, Node, Incident, Error relationships
- **Graph projection ETL** — templates + events → graph
- **GraphRAG** — hybrid vector similarity + graph neighborhood retrieval
- **Multi-agent system** — Planner, Log Analyst, Explainer, Runbook, Operator
- **Temporal anomaly detection** — new templates, frequency spikes, missing expected patterns
- **Deterministic routing + ReAct** — Python picks tools, model reasons in text format

### The Question It Should Answer
Not "show me the errors" — that's grep.
**"What changed? Is this normal? What should I do about it?"**

---

## Differentiator vs. Existing Tools

| Capability | Datadog/Grafana/ELK | DevMesh |
|-----------|---------------------|---------|
| Log aggregation | Yes | Yes |
| Keyword search | Yes | Yes |
| Semantic search | No (or limited) | Yes (vector similarity on canonical templates) |
| Template compression | No | Yes (157x) |
| Natural language queries | No | Yes (LLM with tool calling) |
| Infrastructure memory | No | Planned (Phase 4) |
| Temporal drift detection | Manual alerting rules | Planned (automatic) |
| Cost | $$$$ (per-GB pricing) | **$0** (local inference) |
| Data residency | Cloud | **Fully on-premises** |
