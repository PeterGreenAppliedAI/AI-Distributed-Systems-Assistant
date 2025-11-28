# AI Distributed Systems Assistant — Architecture

## Goals

- Local-first assistant for distributed systems (clusters, services, nodes)
- Correlate **logs**, **metrics**, and **topology** into a shared knowledge graph
- Use **GraphRAG + multi-agent workflows** to:
  - Explain incidents
  - Surface root-cause hints
  - Connect to relevant runbooks and docs

## Components

### 1. API / Orchestration (FastAPI)

- Hosts HTTP endpoints for:
  - Queries (`/query`)
  - Health checks (`/health`)
  - Admin / ingestion endpoints (planned)
- Orchestrates:
  - Agent calls
  - Retrieval over GraphRAG
  - LLM calls for extraction / reasoning

### 2. Storage & Knowledge

#### MariaDB (with vector)

- Tables (planned):
  - `documents` — raw docs, runbooks, descriptions
  - `chunks` — text chunks with metadata
  - `embeddings` — vector column for chunks
  - `incidents` — incident metadata
  - `metrics_snapshots` — serialized metrics

- Used for:
  - Semantic retrieval
  - Hybrid search with Neo4j neighborhoods

#### Neo4j

- Nodes:
  - `Service`
  - `Node`
  - `Pod` (optional)
  - `Incident`
  - `LogSnippet`
  - `MetricSnapshot`
  - `Runbook`
  - `Recommendation`

- Relationships (examples):
  - `(:Incident)-[:AFFECTS]->(:Service)`
  - `(:Incident)-[:OBSERVED_ON]->(:Node)`
  - `(:Incident)-[:HAS_LOG]->(:LogSnippet)`
  - `(:Incident)-[:HAS_METRIC]->(:MetricSnapshot)`
  - `(:Service)-[:RUNS_ON]->(:Node)`
  - `(:Incident)-[:RELATED_RUNBOOK]->(:Runbook)`

Neo4j provides the **graph topology** and relationship context that GraphRAG uses.

### 3. Observability Stack

- **Loki** — log ingestion / querying
- **Prometheus** — metrics scraping
- **Grafana** — dashboards for:
  - system health
  - incident timelines
  - assistant activity (later)

The assistant consumes data exposed by Loki/Prometheus and writes structured summaries into Neo4j + MariaDB.

### 4. Models / LLM Layer

- **Embedding model**
  - Embeds text chunks (logs, runbooks, explanations)
  - Embeds queries and incident descriptions

- **Reasoning / retrieval model**
  - Performs:
    - query rewriting
    - entity/relationship extraction
    - Cypher + SQL template generation
    - explanation synthesis

- **Optional reranker**
  - Reranks retrieved chunks / graph neighborhoods
  - Helps improve context selection

All models are intended to be **open-source and locally hosted** (Nemotron family is a strong candidate).

### 5. Agents

Initial agents:

- **Planner Agent**
  - Decides which specialist agent(s) to invoke:
    - Log Agent
    - Topology Agent
    - Explainer Agent
    - Runbook Agent

- **Log Agent**
  - Queries Loki
  - Detects patterns / anomalies in logs
  - Creates/updates `Incident` + `LogSnippet` nodes
  - Attaches `MetricSnapshot` references where relevant

- **Topology Agent**
  - Ingests cluster/service topology from external sources (K8s API, static config, etc.)
  - Maintains `Service`, `Node`, `Pod` nodes and edges

- **Explainer Agent**
  - Uses GraphRAG to fetch:
    - incident details
    - affected services/nodes
    - relevant runbooks / docs
  - Generates natural language explanations and recommendations

- **Runbook Agent**
  - Ingests runbooks / documentation into MariaDB + Neo4j
  - Links `Incident` nodes to `Runbook` nodes

### 6. GraphRAG Strategy

- Retrieve relevant **graph neighborhood** in Neo4j:
  - start at `Incident` or `Service`
  - expand to related nodes (logs, metrics, runbooks)
- Retrieve **semantic neighbors** in MariaDB (vector search):
  - log text
  - doc sections
  - prior incident descriptions
- Merge + rerank context, then provide to LLM for answering.

## Roadmap (Architecture)

1. Minimal stack with:
   - FastAPI healthcheck
   - MariaDB + Neo4j + Loki + Prometheus + Grafana via `docker-compose`
2. Basic Neo4j schema & sample data
3. Simple GraphRAG endpoint for querying sample incidents
4. Loki integration + Log Agent creating `Incident` nodes
5. Metrics integration + `MetricSnapshot` nodes
6. Multi-agent orchestration with Planner + Explainer agents
