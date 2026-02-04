# DevMesh Platform

**AI-Native Observability for Local/Hybrid Infrastructure**

Local-first observability platform with semantic search, log canonicalization, and LLM-powered analysis.
Built with MariaDB (vector search), Ollama, and open-source LLMs.

---

## Current Status (February 2026)

| Metric | Value |
|--------|-------|
| Logs ingested | 928K+ |
| Unique templates | 5,944 (151x compression) |
| Active nodes | 7 |
| Storage | ~17.6 GB |
| Retention | 90 days (TTL cleanup) |

### What's Working

- **Multi-node log streaming** from 7 Linux nodes via journald shippers
- **Semantic search** over logs and templates using MariaDB VECTOR + cosine similarity
- **Log canonicalization** — PIDs, timestamps, IPs, UUIDs normalized to tokens
- **Template deduplication** — 928K raw logs → 5,944 unique patterns
- **LLM analysis** via `/analyze` endpoint with tool calling (nemotron-3-nano)
- **Web UI** at `/ui` — dark theme, filters, markdown rendering
- **TTL cleanup** — automatic pruning of logs and templates older than 90 days

---

## Quick Start

```bash
# 1. Set up environment
git clone https://github.com/your-org/devmesh-platform.git
cd devmesh-platform
cp .env.example .env
# Edit .env with your MariaDB credentials and Ollama gateway URL

# 2. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Create database schema
python db/database.py

# 4. Run migrations
python db/migrations/001_add_log_hash.py
python db/migrations/002_add_embedding_vector.py
python db/migrations/003_create_log_templates.py

# 5. Start the API
python main.py
# API available at http://localhost:8000
# Web UI at http://localhost:8000/ui

# 6. Start log shipper (on each node)
python -u shipper/log_shipper_daemon.py
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Linux Nodes (7)                                                │
│  journald → log_shipper_daemon.py → POST /ingest/logs           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  DevMesh API (FastAPI)                                          │
│  ├── /ingest/logs      — batch ingestion + dedup + embedding    │
│  ├── /query/logs       — filtered log queries                   │
│  ├── /search/logs      — semantic search (vector similarity)    │
│  ├── /search/templates — search canonical patterns              │
│  ├── /analyze          — LLM-powered natural language analysis  │
│  └── /ui               — web interface                          │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│  MariaDB 12.0.2          │    │  Ollama Gateway          │
│  ├── log_events          │    │  ├── nomic-embed-text    │
│  │   (928K rows)         │    │  │   (embeddings)        │
│  │   + embedding_vector  │    │  └── nemotron-3-nano     │
│  │   + template_id       │    │       (LLM analysis)     │
│  └── log_templates       │    └──────────────────────────┘
│      (5,944 patterns)    │
│      + HNSW vector index │
└──────────────────────────┘
```

---

## API Endpoints

### Ingestion
- `POST /ingest/logs` — Batch log ingestion (auto-embeds, deduplicates, links to templates)

### Query
- `GET /query/logs` — Filter by service, host, level, time range
- `GET /search/logs` — Semantic search: "find logs similar to this error"
- `GET /search/templates` — Search canonical patterns, returns examples

### Analysis
- `POST /analyze` — Natural language questions → LLM tool calls → synthesized answer

### System
- `GET /health` — Health check
- `GET /info` — Platform info
- `GET /ui` — Web interface

**API Docs**: http://localhost:8000/docs

---

## Database Schema

### `log_events` — Raw logs
```sql
CREATE TABLE log_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME(6) NOT NULL,
    source VARCHAR(255) NOT NULL,
    service VARCHAR(255) NOT NULL,
    host VARCHAR(255) NOT NULL,
    level ENUM('DEBUG','INFO','WARN','WARNING','ERROR','CRITICAL','FATAL') NOT NULL,
    message TEXT NOT NULL,
    log_hash CHAR(32),                    -- Deduplication hash
    embedding_vector VECTOR(4096),        -- Semantic embedding
    template_id BIGINT,                   -- Link to canonical template
    -- ... indexes omitted for brevity
);
```

### `log_templates` — Canonical patterns
```sql
CREATE TABLE log_templates (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    template_hash CHAR(32) UNIQUE,        -- Hash of (service, level, canonical_text)
    canonical_text TEXT NOT NULL,         -- Normalized message (PIDs → <PID>, etc.)
    service VARCHAR(255),
    level VARCHAR(20),
    embedding_vector VECTOR(4096) NOT NULL,
    canon_version VARCHAR(10),            -- Canonicalization rule version (v1)
    event_count BIGINT DEFAULT 1,         -- How many raw logs match this
    first_seen DATETIME(6),
    last_seen DATETIME(6),
    -- HNSW vector index for fast similarity search
);
```

---

## Canonicalization

Raw logs are normalized before template matching:

| Pattern | Replacement |
|---------|-------------|
| `192.168.1.50` | `<IPV4>` |
| `2026-02-04T12:34:56Z` | `<TS>` |
| `pid=12345` | `pid=<PID>` |
| `a1b2c3d4-e5f6-...` | `<UUID>` |
| `0x7fff5fbff8c0` | `<HEX>` |
| `user=john` | `user=<USER>` |
| `took 1.234s` | `took <DUR>` |

This reduces 928K raw logs → 5,944 unique templates (151x compression).

---

## Deployed Nodes

| Node | IP | Status | Logs |
|------|-----|--------|------|
| dev-services | 10.0.0.20 | Active | 382K+ |
| gpu-node | 10.0.0.19 | Active | 47K+ |
| gpu-node-3060 | 10.0.0.14 | Active | 40K+ |
| electrical-estimator | 10.0.0.13 | Active | 16K+ |
| teaching | 192.168.1.227 | Active | 14K+ |
| mariadb-vm | 10.0.0.18 | Active | 14K+ |
| postgres-vm | 192.168.1.220 | Active | 3K+ |
| monitoring-vm | 10.0.0.17 | Pending | — |

---

## Project Phases

### Phase 1: Logging Foundation — Complete
Real-time log collection, deduplication, FastAPI ingestion/query API.

### Phase 2: Embeddings & Semantic Search — Complete
- MariaDB VECTOR columns with HNSW indexing
- nomic-embed-text embeddings via Ollama batch API
- Semantic search endpoints
- Log canonicalization (v1 rules)
- Template deduplication layer

### Phase 3: LLM Reasoning — In Progress
- Tool-calling LLM (nemotron-3-nano via Ollama)
- 5 tools: `search_templates`, `query_logs`, `get_template_stats`, `get_service_overview`, `get_log_context`
- `/analyze` endpoint for natural language queries
- Web UI with markdown rendering

### Phase 4: Knowledge Graph — Planned
- FalkorDB for service/incident relationships
- GraphRAG: vector similarity + graph traversal
- Multi-agent system for incident analysis

---

## Operations

### Deploy shipper to a new node
```bash
# Create package
tar czf /tmp/devmesh-shipper.tar.gz \
    shipper/log_shipper_daemon.py \
    shipper/filter_config.py \
    shipper/filter_config.yaml \
    deploy/install_shipper.sh

# Copy and install
scp /tmp/devmesh-shipper.tar.gz user@NODE:/tmp/
ssh user@NODE "cd /tmp && tar xzf devmesh-shipper.tar.gz && sudo ./deploy/install_shipper.sh NODE_NAME API_HOST"
```

### Check status
```bash
# Shipper
sudo systemctl status devmesh-shipper
sudo journalctl -u devmesh-shipper -n 50

# API
curl http://10.0.0.20:8000/health

# Semantic search
curl "http://10.0.0.20:8000/search/templates?query=connection+refused&limit=5"
```

### TTL cleanup (cron)
```bash
# Runs automatically, or manually:
python infra/ttl_cleanup.py --days 90 --dry-run
```

---

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — Instructions for Claude Code
- **[NEXT_STEPS.md](NEXT_STEPS.md)** — Current status and immediate TODOs
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System design and data flow

---

## License

MIT

---

**Built by**: Pete Green / DevMesh Services

Co-Authored-By: Claude <noreply@anthropic.com>
