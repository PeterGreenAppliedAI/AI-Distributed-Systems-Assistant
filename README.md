DevMesh AI Platform – Phase 1 PRD

Title: Eager Assistant Platform Foundation (Phase 1)
Author: Pete / DevMesh
Version: 1.0
Status: Draft for build

1. Purpose

Build the foundational components of the DevMesh AI agent ecosystem in a way that is:

contract-driven

modular

replaceable at every layer

secure-by-design

aligned with industry best practices (SOLID, DRY, YAGNI, KISS/LOS)

scalable

compatible with Claude Code, CrewAI, local inference, and remote inference

This phase focuses on:

establishing a clean MCP

building the ingestion → semantic extraction → RAG → KG → retrieval pipeline

defining the agent framework (CrewAI)

creating an initial operator UI

setting the blueprint for iterative agent addition

This PRD defines the contract for all future components.

2. Problem Statement

Current prototype:

mixes logic with orchestration

has MCP wrapped around manual steps

lacks formal contracts

has inconsistent ingestion logic

has no semantic extraction tuning loop

has no unified RAG/KG subsystem

uses terminal-only interfaces

cannot run autonomous cycles (outreach, research, ingestion, anomaly detection, etc.)

lacks modular agents with clear boundaries

This causes:

high coupling

low replaceability

difficulty in testing

fragile workflows

inability to scale or expand cleanly

Phase 1 must establish the foundation so future agents are plug-and-play.

3. Goals & Non-Goals
3.1 Goals

Define and implement a clean MCP server

all tools expose strict JSON input/output schemas

no business logic inside the MCP server

MCP = contracts + pure operations

Build the unified ingestion pipeline

document detection

LLM-assisted extraction

markdown normalization

chunking

semantic metadata extraction treated as a model with tuning

Implement RAG subsystem with proper semantic extraction tuning

iterative improvement

eval sets

observability logs

stateless retrieval API

Build version-1 Knowledge Graph schema

entities, relations, provenance

stored in MariaDB with vector engine

fully replaceable

Build the retrieval service

KG → vector search → context assembly

unified interface

exposed as a tool to agents

Implement CrewAI foundational agent roles

Planner

Research agent

Extractor agent

Reviewer agent

Create a simple operator console UI (Streamlit/OpenWebUI)

model selection

MCP tool tester

retrieval viewer

ingestion logs

KG visualization (basic)

Establish logging, traceability, and evaluation

model evals

extraction evals

retrieval evals

full observability

Ship a working end-to-end pipeline

operator uploads file

ingestion

extraction

chunking

RAG

KG update

retrieval

agentical workflow execution

3.2 Non-Goals

Building a production UI

Building 10+ agents

Full automation (campaign loops, anomaly loops, etc.)

Multi-node scheduling

LLM fine-tuning

Optimization for speed or cost

Workforce integrations

Phase 1 = foundation, not empire.

4. Users / Personas
Primary User: Operator (You)

Needs:

upload data

test ingestion

test retrieval

examine KG entities

run agents

debug pipelines

inspect logs

Secondary User: Developer (Also you)

Needs:

clean modular code

well-documented contracts

testable tools

easy swap of models/engines

reproducible workflows

5. High-Level Architecture
5.1 Core Pillars
                 ┌──────────────────────────┐
                 │      Operator UI         │
                 └─────────────┬────────────┘
                               │
                     (CrewAI orchestrator)
                               │
                   ┌───────────▼───────────┐
                   │         MCP            │
                   │ (contract layer only)  │
                   └─┬──────────┬──────────┘
     ingestion.tools │          │ retrieval.tools
                     │          │
             ┌───────▼──────┐   ▼──────────────┐
             │ Ingestion     │                  │
             │ Pipeline      │  Retrieval API   │
             └───┬──────┬───┘                  │
                 │      │                      │
        semantic │     RAG                     │
      extraction │     KG lookup               │
                 │      │                      │
                 ▼      ▼                      ▼
            ┌────────────────────────────────────────┐
            │         MariaDB + Vector Engine        │
            │   (Documents, Chunks, Entities, KG)    │
            └────────────────────────────────────────┘


Every interaction is governed by contracts, not ad-hoc calls.

6. Functional Requirements
6.1 MCP Server

must load tools dynamically

each tool must have:

schema.json file

input_schema & output_schema

descriptive metadata

must log:

request

response

latency

model used

Tools V1:

file.ingest

doc.extract

doc.chunk

doc.semantic_extract

rag.query

kg.resolve_entities

kg.append

6.2 Ingestion Pipeline

detect file type (pdf, docx, html, txt)

normalize into markdown

LLM-assisted structural extraction

chunk into semantically meaningful blocks

store in DB

6.3 Semantic Extraction (Model)

treat as a trainable unit

maintain evaluation dataset

maintain revision logs

tuning rounds

accuracy targets

6.4 RAG Subsystem

vector search

filtering

scoring

ranking

return with provenance

must work even if KG is empty

6.5 Knowledge Graph

store:

entities

relationships

types

provenance

must allow KG-only queries

must integrate with RAG

6.6 Retrieval API

input:

query

top_k

entity-first vs vector-first mode

output:

merged context

6.7 CrewAI Agents

Planner

decides which tools to call

Research Agent

federated search (Brave, Tavily, others)

ingestion of results

basic dedupe

Extraction Agent

runs document → markdown → chunk → extract → db

Reviewer Agent

inspects outputs

checks quality

6.8 Operator UI

upload documents

run tools

inspect KG

test retrieval

visualize logs

run agents manually

6.9 Observability

logs

metrics

traces

error alerts

eval dashboards

7. Constraints & Engineering Principles
Secure-by-Design

no outbound code execution without user approval

no unvalidated input to DBs

strict schemas

sandbox LLM tool execution

minimal privileges

zero trust between components

SOLID

single-responsibility for each component

agents do not perform business logic

MCP tools do not perform orchestration

pipelines do not hold state

DRY

shared utils extracted

DB queries centralized

schemas re-used

YAGNI

build only what’s needed for Phase 1

no premature agent additions

KISS / LOS

prefer simple, readable flows

avoid over-engineering pipelines

default to naive implementation first

Replaceability

any component (model, DB, tool, agent) must be swappable via configuration

Modularity

every module stands on its own

no cross-imports

no circular dependencies

Contracts Everywhere

all interactions must go through schemas

schemas are versioned

code fails fast if contract broken

8. Acceptance Criteria
End-to-End Demo Works

Operator can:

upload doc

ingestion runs

semantic extraction populates DB

KG updated

RAG retrieval works

CrewAI agent runs multi-step flow

UI shows logs + results

Replaceability Proof

swap local model with remote model (OpenRouter) without code changes

swap chunking algorithm via config

swap DB table names via config

swap agent model via config

Security

static analysis passes

tool schemas validated

no arbitrary code execution

Performance

ingestion under 5 seconds for small docs

retrieval under 800ms

9. Phase 1 Deliverables

MCP server

ingestion pipeline

semantic extraction module

RAG subsystem

KG schema

retrieval API

4 CrewAI agents

operator console UI

observability stack

documentation

example workflows

10. Future Phases (Not in Scope)

full automation loops

anomaly-based triggers

outreach automation

multi-agent persistent projects

fine-tuned models

advanced UI dashboards

distributed inference

multi-node orchestration
