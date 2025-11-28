# DevMesh Platform (formerly AI Distributed Systems Assistant)

**AI-Native Observability for Local/Hybrid Infrastructure**

Local-first GraphRAG and multi-agent system for analyzing distributed infrastructure, logs, and metrics.
Built with MariaDB, FalkorDB (planned), Loki, Prometheus, and open-source LLMs.

---

## 🎉 Phase 1 Complete! Real-Time Log Streaming Operational

**Status**: ✅ Phase 1 Complete - Real-Time Logging Foundation
**Date**: November 28, 2025
**Next**: 24hr burn-in test → Phase 2 (Embeddings & Semantic Search)

### What's Working Right Now

- ✅ **Real-time log streaming** from dev-services node to MariaDB
- ✅ **11,733 logs ingested** (historical + continuous real-time)
- ✅ **HTTP API** for log ingestion and querying
- ✅ **No blind spots** - logs stream continuously as they're generated
- ✅ **Indexed, queryable storage** in MariaDB with microsecond timestamps
- ✅ **Batch ingestion** with crash recovery and cursor persistence
- ✅ **Ready to scale** to 9 nodes on 10.0.0.x network

### Quick Start (Phase 1)

```bash
# 1. Set up environment
cd /home/tadeu718/devmesh-platform
cp .env.example .env
# Edit .env with your MariaDB credentials

# 2. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Create database schema
python db/database.py

# 4. Start the API
python main.py
# API available at http://localhost:8000

# 5. Start real-time log streaming
python -u shipper/log_shipper_daemon.py

# 6. Query logs
curl "http://localhost:8000/query/logs?service=docker.service&level=ERROR&limit=10"
```

**For detailed Phase 1 documentation**: See [`PHASE1_FOUNDATION.md`](PHASE1_FOUNDATION.md)

---

## What is DevMesh Platform?

DevMesh is an **AI-native observability platform** designed specifically for local and hybrid AI infrastructure.

Traditional tools (Datadog, Elastic, Grafana) are:
- Dashboard-centric, not AI-aware
- Metric-focused, not context-aware
- Not designed for local GPU infrastructure
- Alert-based, not explanation-based

**DevMesh provides:**
1. **Real-time log and metric collection** across entire cluster
2. **Semantic correlation** using vector embeddings and knowledge graphs
3. **Context-aware retrieval** combining similarity search + graph traversal
4. **LLM-powered reasoning** using local models (Phi 4, Nemotron, etc.)
5. **Natural language explanations** of incidents and system behavior
6. **Safe, scoped actions** triggered by AI agents

---

## Architecture Overview

### Phase 1: Real-Time Logging Foundation (✅ Complete)

```
┌─────────────────┐
│  Linux Nodes    │
│  (journald)     │ ← System logs from all services
└────────┬────────┘
         │ Real-time JSON stream
         ↓
┌─────────────────────────────────┐
│  Log Shipper Daemon             │
│  - Tails journald with -f       │
│  - Batches logs (50 per batch)  │
│  - Sends to API via HTTP POST   │
│  - Tracks cursor for recovery   │
└────────┬────────────────────────┘
         │ POST /ingest/logs
         ↓
┌─────────────────────────────────┐
│  DevMesh API (FastAPI)          │
│  - POST /ingest/logs            │
│  - GET  /query/logs             │
└────────┬────────────────────────┘
         │ SQL INSERT
         ↓
┌─────────────────────────────────┐
│  MariaDB (10.0.0.18)            │
│  Table: log_events              │
│  - Indexed by timestamp, host   │
│  - Microsecond precision        │
│  - JSON metadata support        │
└─────────────────────────────────┘
```

### Planned Architecture (Phases 2-4)

```
┌──────────────────────────────────────────────────────────┐
│                    Ingestion Layer                        │
├──────────────────────────────────────────────────────────┤
│  Topology Agent  │  Log Agent (✅)  │  Metrics Agent      │
│  (K8s, Proxmox)  │  (journald)      │  (Prometheus)       │
└──────────────────┴──────────────────┴──────────────────┬─┘
                                                         │
┌────────────────────────────────────────────────────────┼─┐
│                    Knowledge Layer                     │ │
├────────────────────────────────────────────────────────┼─┤
│  MariaDB (Vector Store)        │  FalkorDB/Neo4j       │ │
│  - log_events (✅)              │  - Service nodes      │ │
│  - log_embeddings              │  - Incident nodes     │ │
│  - metric_events               │  - Relationships      │ │
│  - Vector similarity search    │  - Graph traversal    │ │
└────────────────────────────────┴───────────────────────┴─┘
                         │
┌────────────────────────┼────────────────────────────────┐
│               GraphRAG Retrieval                        │
│  Hybrid: Vector Similarity + Graph Neighborhood        │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────┐
│                  Agent Layer (CrewAI)                   │
├─────────────────────────────────────────────────────────┤
│  Planner │ Log Analyst │ Explainer │ Runbook │ Operator│
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────┐
│                 LLM Reasoning (Local)                   │
├─────────────────────────────────────────────────────────┤
│  Orchestration: Phi 4 (A5000)                           │
│  Deep Analysis: Nemotron / Larger Model (DGX)           │
└─────────────────────────────────────────────────────────┘
```

---

## Tech Stack

### Currently Deployed (Phase 1)
- **API**: FastAPI 0.109, Python 3.10
- **Database**: MariaDB 12.0.2 with JSON support
- **Log Collection**: Custom Python daemon (systemd journald integration)
- **Deployment**: Bare metal (dev-services node)

### Planned (Phases 2-4)
- **Embeddings**: Sentence-BERT, all-MiniLM-L6, or Nemotron embedding models
- **Graph Database**: FalkorDB or Neo4j Community
- **Observability Sources**: Prometheus (metrics), Loki (alternative log source)
- **LLM Models**: Phi 4, Nemotron instruct, or similar local models
- **Agent Framework**: CrewAI for multi-agent orchestration
- **Interfaces**: Streamlit console, Discord/Telegram bots, Open WebUI integration

---

## Roadmap

### ✅ Phase 1: Logging Foundation (Complete - Nov 28, 2025)

**Goal**: Real-time log streaming from nodes to queryable storage

**Delivered**:
- MariaDB `log_events` table with indexes
- FastAPI ingestion and query API
- Real-time log shipper daemon
- 11,733 logs ingested from dev-services
- Batch ingestion with crash recovery
- Comprehensive documentation

**Next**: 24hr burn-in test, then multi-node deployment

### 🔄 Phase 1.5: Production Hardening (Week of Nov 29, 2025)

**Goal**: Make Phase 1 production-ready and scalable

- TTL/cleanup job (90-day retention)
- Prometheus metrics from API (`/metrics`)
- Grafana dashboard for ingestion monitoring
- Deploy to remaining 8 nodes (10.0.0.13-19)
- systemd service setup for all nodes

### 📅 Phase 2: Embeddings & Semantic Search (Weeks 2-3)

**Goal**: "Find logs similar to this error message"

- Select and deploy local embedding model (A5000 GPU)
- Create `log_embeddings` table
- Embedding worker (background job)
- Semantic search API endpoint
- **Exit criteria**: Can find similar errors via semantic search

### 📅 Phase 3: Retrieval & LLM Reasoning (Weeks 4-6)

**Goal**: "Explain what happened during this incident"

- Vector + time-based retrieval
- LLM integration (Phi 4 for orchestration, larger model for synthesis)
- Explanation API endpoint
- **Exit criteria**: Natural language explanations of log patterns

### 📅 Phase 4: Knowledge Graph & Agents (Weeks 6-8)

**Goal**: Multi-agent incident analysis with knowledge graph

- FalkorDB/Neo4j deployment
- Graph schema (`Service`, `Node`, `Incident`, `Error`)
- Graph projection ETL (logs → graph)
- CrewAI multi-agent system
- Operator console (Streamlit or web UI)
- **Exit criteria**: Agents can correlate incidents across services

---

## Database Schema (Phase 1)

### `log_events` Table

```sql
CREATE TABLE log_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME(6) NOT NULL,           -- Microsecond precision (UTC)
    source VARCHAR(255) NOT NULL,             -- 'journald', 'promtail', etc.
    service VARCHAR(255) NOT NULL,            -- Systemd unit or app name
    host VARCHAR(255) NOT NULL,               -- Node name (e.g., 'dev-services')
    level ENUM('DEBUG','INFO','WARN','WARNING','ERROR','CRITICAL','FATAL') NOT NULL,
    trace_id VARCHAR(64) DEFAULT NULL,        -- Distributed tracing support
    span_id VARCHAR(32) DEFAULT NULL,
    event_type VARCHAR(100) DEFAULT NULL,     -- 'http_request', 'db_error', etc.
    error_code VARCHAR(50) DEFAULT NULL,      -- 'ECONNRESET', 'HTTP_500', etc.
    message TEXT NOT NULL,                    -- Actual log message
    meta_json JSON DEFAULT NULL,              -- Extra metadata (flexible)

    INDEX idx_timestamp (timestamp),
    INDEX idx_service (service),
    INDEX idx_host (host),
    INDEX idx_level (level),
    INDEX idx_service_timestamp (service, timestamp),
    INDEX idx_host_timestamp (host, timestamp),
    INDEX idx_trace_id (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Design highlights**:
- Microsecond timestamps for precise event ordering
- Compound indexes for efficient queries
- JSON metadata for node-specific fields
- Ready for distributed tracing correlation

---

## API Endpoints

### System

- `GET /health` - Health check
- `GET /info` - Platform information

### Ingestion

- `POST /ingest/logs` - Batch log ingestion (accepts array of log objects)

### Query

- `GET /query/logs` - Query logs with filters:
  - `service` - Filter by service name
  - `host` - Filter by node/host
  - `level` - Filter by log level (DEBUG, INFO, WARN, ERROR, etc.)
  - `start_time` / `end_time` - Time window (UTC)
  - `limit` / `offset` - Pagination

**Example**:
```bash
curl "http://localhost:8000/query/logs?service=loki.service&level=ERROR&start_time=2025-11-28T00:00:00Z&limit=100"
```

**API Documentation**: http://localhost:8000/docs

---

## Current Metrics (as of Nov 28, 2025)

```
Node: dev-services (10.0.0.20)
Total logs: 11,733
Storage: ~5.9 MB (log data) + ~2-4 MB (indexes) ≈ 8-10 MB total

Top services:
  user@1000.service    10,539 logs  (Loki, user processes)
  kernel                  751 logs  (kernel messages)
  session-*.scope         184 logs  (user sessions)
  cron.service             79 logs  (cron jobs)

Log levels:
  INFO     90.2%  (10,585 logs)
  WARN      6.3%  (742 logs)
  DEBUG     0.4%  (44 logs)
  ERROR     0.01% (1 log)
```

**Projected growth** (9 nodes total):
- Daily: ~50 MB/day
- 90 days: ~4.5 GB

---

## Deployment

### Single Node (dev-services)

Already deployed and operational. See Quick Start above.

### Multi-Node Deployment

See [`PHASE1_FOUNDATION.md`](PHASE1_FOUNDATION.md) § Deployment Guide for step-by-step instructions to deploy to additional nodes.

**Summary**:
1. Copy shipper code to new node
2. Edit `.env` with unique `NODE_NAME`
3. Set up systemd service
4. Start daemon
5. Verify logs appearing in database with correct `host` field

---

## Operations

### Monitor Daemon

```bash
# Check daemon status
systemctl status devmesh-shipper

# View real-time logs
journalctl -u devmesh-shipper -f

# Check cursor position
cat /opt/devmesh/shipper/cursor.txt
```

### Monitor API

```bash
# Health check
curl http://localhost:8000/health

# Check recent logs
curl "http://localhost:8000/query/logs?limit=10"
```

### Monitor Database

```sql
-- Check table size
SELECT
  ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb,
  table_rows
FROM information_schema.tables
WHERE table_schema = 'devmesh' AND table_name = 'log_events';

-- Latest logs
SELECT timestamp, host, service, level, message
FROM log_events
ORDER BY timestamp DESC
LIMIT 10;
```

---

## Documentation

- **[PHASE1_FOUNDATION.md](PHASE1_FOUNDATION.md)** - Comprehensive Phase 1 documentation
  - Problem statement & design decisions
  - Architecture deep dive
  - Deployment guide
  - Operations & troubleshooting
  - Roadmap for Phases 2-4

- **[README.md](README.md)** - This file (project overview)

---

## Contributing

This project is currently in active development. Phase 1 is complete and operational.

For questions, issues, or contributions:
- Open an issue on GitHub
- See roadmap above for upcoming work

---

## License

MIT (to be added)

---

**Built by**: Pete Green / DevMesh Services
**Powered by**: Claude Code (Anthropic)

🤖 Phase 1 generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
