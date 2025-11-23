AI Distributed Systems Assistant

Local-first GraphRAG + multi-agent system for analyzing distributed infrastructure, logs, metrics, and cluster topology.
Ingests real observability data (Loki, Prometheus), builds a unified knowledge graph (Neo4j + MariaDB vectors), and uses local LLMs to explain incidents and cluster behavior.

Why this exists

Modern distributed systems generate too much data across too many surfaces — logs, metrics, events, topologies, runbooks, configs.
Most teams piece this together manually.

This project builds an AI-native observability layer:

Understand your cluster topology

Correlate logs → metrics → services → incidents

Store structured knowledge in a graph

Retrieve context using GraphRAG

Use local LLMs to explain what’s happening

Suggest runbooks or next actions

All locally, without sending your data to any cloud LLM.


## High-level architecture

```
       +----------------------+
       |      User Query      |
       +----------+-----------+
                  |
                  v
       +----------+-----------+
       |       Planner Agent  |
       +----+---------+-------+
            |         |
   +--------+         +---------+
   |                            |
   v                            v
+--+----------------+    +------+-----------------+
|     Log Agent     |    |    Topology Agent      |
|  (Loki ingestion) |    | (services/nodes/pods)  |
+--------+----------+    +-----------+------------+
         |                           |
         v                           v
+--------+----------------------------+----------+
|              Knowledge Graph (Neo4j)           |
|   Services • Nodes • Incidents • Runbooks     |
+-------------------+---------------------------+
                    |
       +------------+-------------+
       |      Vector Store        |
       |   (MariaDB w/ vectors)   |
       +------------+-------------+
                    |
                    v
           +--------+--------+
           |  Explainer Agent |
           +--------+---------+
                    |
                    v
           Natural language answer
```


Key capabilities
1. Log & event ingestion

Pulls log streams from Loki

Extracts incidents, anomalies, patterns

Creates Incident, LogSnippet graph nodes

2. Metrics ingestion

Pulls cluster + service metrics from Prometheus

Attaches MetricSnapshot nodes to incidents

3. Topology ingestion

Automatically maps:

Services

Pods

Nodes

Deployments
to graph relationships that GraphRAG can reason over.

4. Knowledge graph

Neo4j ties together:

incidents

services

nodes

metrics

logs

runbooks

Agents use this as a shared world model — no private memory silos.

5. Semantic search + graph reasoning

MariaDB vector search + Neo4j graph expansion enables:

retrieving relevant incidents

similar logs

matching runbooks

identifying patterns across services

6. Local LLM integration

You control the models.
Suggested triple-model design:

Embedding model (Nemotron embedding or similar)

Reasoning model (Nemotron instruct or similar)

Optional reranker (small cross-encoder or LLM-based scoring)

Current stack

Backend: FastAPI
Knowledge graph: Neo4j Community
Vector DB: MariaDB 11+ (vector indexes enabled)
Logs: Loki
Metrics: Prometheus
Dashboards: Grafana
Models: Any local LLM (e.g., Nemotron, Llama, Phi, Gemma, etc.)
Orchestration: Hand-rolled agent graph (LangGraph-style)

Roadmap
0. Initial scaffolding ✔ current

Repo structure

Architecture docs

Docker compose stack

FastAPI skeleton

Neo4j schema

1. Minimal ingestion pipeline

Basic Loki ingestion

Basic Prometheus scrape

Build sample topology + seed incidents

2. GraphRAG engine

Hybrid retrieval over Neo4j + MariaDB vectors

Query rewriting and neighborhood expansion

3. Agents

Planner Agent

Log Agent

Topology Agent

Explainer Agent

Runbook Agent

4. Cluster explanation v1

Explain service health

Summaries for incidents

“Why is this node unstable?”

5. Real-time mode

Periodic ingestion

Streaming updates

Incident correlation

6. UI

Optional: small web dashboard or CLI

Getting started
Prereqs

Docker & Docker Compose

Python 3.12 (optional for local dev)

Bring up the stack
docker-compose -f infra/docker-compose.yml up --build

Test the API
curl http://localhost:8000/health

Contributing

This is an early, active project — contributions are welcome.
Planned areas:

retrieval optimization

agent design

graph schemas

log/metrics ingestion adapters

LLM model adapters

UI

devops & deployment
