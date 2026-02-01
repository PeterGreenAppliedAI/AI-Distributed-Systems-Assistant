# DevMesh Platform — Architecture

## Goals

- Local-first observability for distributed systems (clusters, services, nodes)
- Correlate **logs**, **metrics**, and **topology** into a shared knowledge graph
- Use **GraphRAG + multi-agent workflows** to:
  - Explain incidents
  - Surface root-cause hints
  - Connect to relevant runbooks and docs

## Components

### 1. API / Orchestration (FastAPI)

- Hosts HTTP endpoints for:
  - Ingestion (`POST /ingest/logs`)
  - Query (`GET /query/logs`)
  - Semantic search (`GET /search/logs`)
  - Health checks (`GET /health`, `GET /info`)
- Manages shared `httpx.AsyncClient` for embedding gateway calls
- Centralized error handling with domain error types

### 2. LLM Gateway (Ollama)

- **Host**: `192.168.1.184:8001` (DGX Spark)
- **Embedding model**: `qwen3-embedding:8b` (4096 dimensions)
- **Endpoints used**:
  - `/v1/embeddings` — OpenAI-compatible batch endpoint (primary, ~50 texts in 2.4s)
  - `/api/embeddings` — Ollama native single-text endpoint (fallback)
- **Also available**: phi4, mistral:7b-instruct, qwen2.5:7b, qwen2.5-coder:32b, qwen3:14b, qwen2.5:72b
- Config via env: `GATEWAY_URL`, `EMBEDDING_MODEL`, `EMBEDDING_TIMEOUT`

### 3. Storage & Knowledge

#### MariaDB 12.0.2 (Primary Store)

- **Host**: `10.0.0.18`
- **Table**: `log_events`
  - `id` BIGINT PK
  - `log_hash` VARCHAR(16) — SHA256 truncated, for deduplication
  - `timestamp` DATETIME(6) — microsecond precision
  - `source`, `service`, `host`, `level`, `message` — core fields
  - `trace_id`, `span_id`, `event_type`, `error_code` — correlation
  - `meta_json` JSON — flexible metadata
  - `embedding_vector` VECTOR(4096) — nullable, qwen3-embedding:8b
- **Indexes**: timestamp, service, host, level, compound indexes, unique on log_hash
- **Vector search**: `VEC_DISTANCE_COSINE()` with `VEC_FromText()` for input
- **HNSW vector index**: pending (requires NOT NULL, created after backfill)

#### FalkorDB (Planned — Phase 4)

- Nodes: `Service`, `Node`, `Incident`, `LogSnippet`, `MetricSnapshot`, `Runbook`
- Relationships: `AFFECTS`, `OBSERVED_ON`, `HAS_LOG`, `RUNS_ON`, `RELATED_RUNBOOK`
- Provides graph topology and relationship context for GraphRAG

### 4. Log Shipper

- Custom Python daemon per node, tails journald in real-time
- Batches logs (50 per batch), sends via `POST /ingest/logs`
- Cursor-based recovery on restart
- Noise filtering via configurable rules
- Deployed as systemd service on 7 nodes

### 5. Embedding Pipeline

#### Live Ingestion Path
- New logs arrive via `POST /ingest/logs`
- If `embedding_vector` column exists, API calls gateway `/v1/embeddings` in batch
- Embeddings stored inline with INSERT (as `VEC_FromText()` text)
- Failures graceful — logs inserted with NULL embedding

#### Backfill Script (`scripts/backfill_embeddings.py`)
- Processes existing rows with NULL `embedding_vector`
- ID-based cursor for efficient resumption (avoids full table scan)
- Batch gateway calls via `/v1/embeddings`
- Configurable `--batch-size` and `--delay` (thermal management)
- Idempotent — safe to stop/resume

### 6. Models / LLM Layer

- **Embedding model**: qwen3-embedding:8b (4096 dims, running on DGX Spark)
- **Reasoning / retrieval model** (planned): Phi 4 for orchestration, larger model for synthesis
- **Optional reranker** (planned): for retrieved chunk quality
- All models open-source and locally hosted

### 7. Agents (Planned — Phase 4)

- **Planner Agent** — routes to specialist agents
- **Log Agent** — queries logs, detects patterns, creates Incident nodes
- **Topology Agent** — maintains service/node graph from cluster APIs
- **Explainer Agent** — GraphRAG retrieval + natural language explanations
- **Runbook Agent** — ingests docs, links incidents to runbooks

### 8. GraphRAG Strategy (Planned)

- Retrieve **graph neighborhood** in FalkorDB (incident → services → logs → metrics)
- Retrieve **semantic neighbors** in MariaDB (vector similarity search)
- Merge + rerank context, provide to LLM for answering

## Data Flow

```
┌─────────────────┐
│  7 Linux Nodes  │
│  (journald)     │
└────────┬────────┘
         │ Real-time JSON stream
         ↓
┌─────────────────────────────────┐
│  Log Shipper Daemon (per node)  │
│  - Tails journald with -f      │
│  - Batches logs (50/batch)     │
│  - Noise filtering             │
└────────┬────────────────────────┘
         │ POST /ingest/logs
         ↓
┌─────────────────────────────────┐      ┌──────────────────────────┐
│  DevMesh API (FastAPI)          │─────→│  LLM Gateway (Ollama)    │
│  - POST /ingest/logs            │      │  192.168.1.184:8001      │
│  - GET  /query/logs             │←─────│  qwen3-embedding:8b      │
│  - GET  /search/logs            │      │  (4096-dim vectors)      │
└────────┬────────────────────────┘      └──────────────────────────┘
         │ SQL INSERT (with embedding)
         ↓
┌─────────────────────────────────┐
│  MariaDB 12.0.2 (10.0.0.18)    │
│  Table: log_events              │
│  - 519K+ rows, 7 nodes         │
│  - VECTOR(4096) embeddings      │
│  - VEC_DISTANCE_COSINE search   │
└─────────────────────────────────┘
```

## Roadmap

1. ~~Phase 1: Logging foundation~~ (complete)
2. **Phase 2: Embeddings & semantic search** (active)
   - Canonicalization & template dedup pipeline (next)
3. Phase 3: Retrieval & LLM reasoning
4. Phase 4: Knowledge graph & multi-agent system
