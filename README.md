# AI Distributed Systems Assistant

Local-first GraphRAG and multi-agent system for analyzing distributed infrastructure, logs, and metrics.  
Built with Neo4j, MariaDB vectors, Loki, Prometheus, Grafana, and open-source LLMs.

## What is this?

This project is an AI assistant for distributed systems:

- Ingests **topology** (services, nodes, pods)
- Ingests **logs** (via Loki) and **metrics** (via Prometheus)
- Stores long-term knowledge in **MariaDB (with vector)** and **Neo4j**
- Uses **GraphRAG** + multi-agent workflows to:
  - Explain incidents and cluster behavior
  - Correlate logs, metrics, and topology
  - Suggest remediation steps and runbooks

Everything is designed to run **locally** on your own hardware.

## Tech stack

- **API / Orchestration**
  - Python 3.12, FastAPI

- **Storage & Retrieval**
  - MariaDB 11+ with vector columns (chunks, embeddings, incidents)
  - Neo4j Community (graph model of services, nodes, incidents, runbooks)

- **Observability**
  - Prometheus (metrics scraping)
  - Loki (log aggregation)
  - Grafana (dashboards)

- **Models (planned)**
  - Embeddings: Nemotron (or compatible OSS embedding model)
  - Reasoning / retrieval: Nemotron instruct (or similar)
  - Optional reranker: small reranker model or LLM-based scoring

## High-level architecture

- **Ingestion layer**
  - Topology Agent: syncs cluster / service graph into Neo4j
  - Log Agent: pulls logs from Loki, creates `Incident` + `LogSnippet` nodes
  - Metrics Agent (planned): pulls metrics from Prometheus, attaches `MetricSnapshot` nodes

- **Knowledge layer**
  - MariaDB: vector store for text chunks, log snippets, doc sections
  - Neo4j: graph of `Service`, `Node`, `Incident`, `Runbook`, etc.
  - GraphRAG: hybrid retrieval over graph + vectors

- **Agents**
  - Planner: routes queries to other agents
  - Explainer Agent: uses GraphRAG to explain incidents / behavior
  - Runbook Agent: connects incidents with relevant documentation / actions

- **Interface**
  - FastAPI service exposing HTTP endpoints (CLI / UI integrations later)

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for more detail.

## Status

> 🚧 Early scaffolding. Not ready for production.

Planned milestones:

1. Initial project structure and containers (this PR)
2. Basic Neo4j schema + seed data
3. Loki + Prometheus + Grafana wired and reachable
4. First GraphRAG query over sample topology + incidents
5. Log Agent + Explainer Agent minimal loop

## Getting started (dev)

Requirements:
- Docker + docker-compose
- (Optional) Python 3.12 for local backend dev

Run the stack:

```bash
docker-compose -f infra/docker-compose.yml up --build
