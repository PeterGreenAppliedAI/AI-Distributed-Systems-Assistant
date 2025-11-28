# DevMesh Platform - Phase 1 Complete ✅

AI-Native Observability Platform for Local Infrastructure

## Phase 1: Logging Foundation

**Status**: ✅ Complete
**Date**: November 28, 2025
**Node**: dev-services

---

## What We Built

### Module 1.1 - Backend & Database ✅
- **FastAPI application** running on port 8000
- **MariaDB `log_events` table** in `devmesh` database (10.0.0.18)
- Endpoints:
  - `GET /health` - Health check
  - `GET /info` - Platform information
  - `POST /ingest/logs` - Batch log ingestion
  - `GET /query/logs` - Query logs with filters

### Module 1.2 - Log Shipper ✅
- **Python script** to extract logs from journald
- Transforms journald format to DevMesh schema
- Batch ingestion (500 logs per batch)
- Successfully ingested **11,372 logs** from last 24 hours

### Module 1.3 - Query API ✅
- Filter logs by:
  - Service name
  - Host name
  - Log level (DEBUG, INFO, WARN, ERROR, CRITICAL, FATAL)
  - Time window (start_time, end_time)
  - Pagination (limit, offset)

---

## Database Stats

```
Total logs in database: 11,373 logs

Top services:
  user@1000.service    10,400 logs (Loki and other user services)
  kernel                  742 logs
  cron.service             79 logs

Log levels:
  INFO                 10,585 logs
  WARN                    742 logs
  DEBUG                    44 logs
  ERROR                     1 log
  CRITICAL                  1 log
```

---

## Exit Criteria Met ✅

From PRD Phase 1:

- ✅ Created `log_events` and `metric_events` schemas in MariaDB
- ✅ Built simple ingestion API for logs
- ✅ Wired dev-services node to send logs
- ✅ Can query: *"Show me all logs for service X in the last 1 hour"*
- ✅ Can query: *"Show me all ERROR logs across all services in the last day"*

---

## Project Structure

```
devmesh-platform/
├── .env                    # Configuration
├── requirements.txt        # Python dependencies
├── main.py                 # FastAPI application
├── models/
│   └── schemas.py         # Pydantic models
├── db/
│   └── database.py        # Database connection & schema
├── api/
│   └── routes.py          # Ingestion & query endpoints
└── shipper/
    └── log_shipper.py     # Journald log shipper
```

---

## Usage

### Start the API Server

```bash
cd /home/tadeu718/devmesh-platform
source venv/bin/activate
python main.py
```

API will be available at: `http://dev-services:8000`

### Run the Log Shipper

```bash
source venv/bin/activate
python shipper/log_shipper.py
```

### Query Examples

**Get recent logs:**
```bash
curl "http://localhost:8000/query/logs?limit=10"
```

**Filter by service:**
```bash
curl "http://localhost:8000/query/logs?service=loki.service&limit=5"
```

**Filter by log level:**
```bash
curl "http://localhost:8000/query/logs?level=ERROR"
```

**Filter by time window:**
```bash
curl "http://localhost:8000/query/logs?start_time=2025-11-28T00:00:00Z&end_time=2025-11-28T12:00:00Z"
```

---

## Next Steps - Phase 2

**Goal**: Embeddings and semantic search

1. Select local embedding model (run on A5000)
2. Implement embedding worker for `log_events`
3. Create `log_embeddings` table
4. Build semantic search API

**Exit criteria for Phase 2**:
- Can ask: *"Find logs similar to this error string"* and get sensible matches

---

## Technical Stack

- **Language**: Python 3.10
- **Web Framework**: FastAPI + Uvicorn
- **Database**: MariaDB 12.0.2
- **Log Source**: systemd journald
- **Node**: dev-services (bare metal)

---

## Database Schema

### log_events

| Field       | Type                  | Description                   |
|-------------|-----------------------|-------------------------------|
| id          | BIGINT AUTO_INCREMENT | Primary key                   |
| timestamp   | DATETIME(6)           | When event occurred (UTC)     |
| source      | VARCHAR(255)          | Component/exporter name       |
| service     | VARCHAR(255)          | Logical service name          |
| host        | VARCHAR(255)          | Node/VM name                  |
| level       | ENUM                  | DEBUG/INFO/WARN/ERROR/etc     |
| trace_id    | VARCHAR(64)           | Distributed trace ID          |
| span_id     | VARCHAR(32)           | Span ID within trace          |
| event_type  | VARCHAR(100)          | Event classification          |
| error_code  | VARCHAR(50)           | Error code (e.g. ECONNRESET)  |
| message     | TEXT                  | Log message content           |
| meta_json   | JSON                  | Extra metadata                |

**Indexes**: timestamp, service, host, level, service+timestamp, host+timestamp, trace_id

---

## Links

- API Docs: http://localhost:8000/docs
- OpenAPI Spec: http://localhost:8000/openapi.json
- Health Check: http://localhost:8000/health

---

**Phase 1 Complete** - Ready for Phase 2: Embeddings & Semantic Search
