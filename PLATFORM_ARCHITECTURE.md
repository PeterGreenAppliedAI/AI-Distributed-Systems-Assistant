# DevMesh Platform Architecture

**A pluggable event-driven memory + detection + investigation engine.**

This document describes the generalized architecture that supports multiple verticals beyond infrastructure logs.

---

## Core Pattern

Every vertical follows the same loop:

```
┌─────────────────────────────────────────────────────────────────┐
│  SOURCE                                                         │
│  (journald, SharePoint, file watcher, upload API, CDC stream)   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  ADAPTER                                                        │
│  Normalizes source-specific events into common Event schema     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  DOMAIN PROCESSOR                                               │
│  Canonicalizes/chunks events into memory units                  │
│  (templates for logs, chunks for docs, metadata for files)      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  MEMORY STORE                                                   │
│  Domain-specific storage with embeddings                        │
│  (templates, chunks, file graph, data catalog)                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  DETECTOR LOOP (shared engine)                                  │
│  Runs domain-specific detectors against memory                  │
│  Writes to shared incident pipeline                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                        (threshold crossed)
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  CONTEXT PACKER                                                 │
│  Gathers bounded context for investigation                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  INVESTIGATOR (CrewAI)                                          │
│  Domain-specific agents, shared orchestration                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  NOTIFICATION                                                   │
│  Shared: Discord, email, dashboard                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## What's Shared vs Pluggable

| Component | Shared | Pluggable |
|-----------|--------|-----------|
| Event schema | Base fields (id, timestamp, source, type) | Domain-specific payload |
| Incident pipeline tables | `detector_state`, `incidents`, `incident_reports` | — |
| Detector loop engine | Scheduling, state management, threshold logic | Detector implementations |
| Context packer | Bundling logic, time windows | What to include per domain |
| CrewAI orchestration | Crew execution, agent handoff | Agent definitions, tools |
| Notification | Discord/email/dashboard integrations | Message formatting |
| Graph engine | FalkorDB queries, traversal | Node/edge schemas per domain |

---

## Adapter Interface

```python
from typing import Protocol, Iterator, Any
from datetime import datetime

class Event:
    """Base event all adapters produce."""
    id: str
    timestamp: datetime
    source: str           # 'journald', 'sharepoint', 'upload', etc.
    event_type: str       # 'log', 'file_created', 'doc_uploaded', etc.
    payload: dict[str, Any]  # Domain-specific data


class SourceAdapter(Protocol):
    """Pulls events from an external source."""

    def stream(self) -> Iterator[Event]:
        """Yield events as they arrive (or poll)."""
        ...

    def backfill(self, since: datetime) -> Iterator[Event]:
        """Replay historical events for initial load."""
        ...


class DomainProcessor(Protocol):
    """Transforms raw events into memory units."""

    def canonicalize(self, event: Event) -> str:
        """Normalize event into canonical form (for dedup/hashing)."""
        ...

    def to_memory_unit(self, event: Event) -> MemoryUnit:
        """Extract what gets stored in memory."""
        ...

    def embed(self, unit: MemoryUnit) -> list[float]:
        """Generate embedding for the memory unit."""
        ...


class MemoryUnit:
    """What gets stored in the memory layer."""
    id: str
    canonical_hash: str
    content: str          # For embedding
    metadata: dict        # Domain-specific (service, path, author, etc.)
    embedding: list[float] | None


class Detector(Protocol):
    """Checks for anomalies against memory."""

    name: str

    def check(self, memory: MemoryStore, state: DetectorState) -> list[Incident]:
        """Run detection logic, return any incidents."""
        ...

    def update_state(self, state: DetectorState) -> DetectorState:
        """Update rolling stats after check."""
        ...
```

---

## Vertical: Infrastructure Logs (current)

**Adapter**: `JournaldAdapter`
- Source: systemd journal via `journalctl -f`
- Events: log lines with service, host, level, message

**Domain Processor**: `LogProcessor`
- Canonicalize: PIDs, IPs, timestamps → tokens
- Memory unit: `log_templates` with canonical_text, service, level

**Memory Store**:
- `log_events` (raw logs)
- `log_templates` (928K → 5,944 patterns)

**Detectors**:
- Error spike per service
- New ERROR template
- Service silence
- Catastrophic primitives (OOM, disk full)
- Correlation (DB error + API restart)

**Graph nodes**: `Service`, `Host`
**Graph edges**: `RUNS_ON`, `DEPENDS_ON`, `CALLS`

**Investigation agents**: Timeline, Root Cause, Impact, Reporter

---

## Vertical: Document Memory

**Adapter**: `DocumentAdapter`
- Source: upload API, watched folder, S3 bucket events
- Events: document created, updated, deleted

**Domain Processor**: `DocumentProcessor`
- Canonicalize: chunk document, extract sections
- Memory unit: chunks with embeddings, document metadata

**Memory Store**:
- `documents` (metadata, source, version)
- `chunks` (text, embedding, doc_id, position)

**Detectors**:
- New topic cluster (embedding drift)
- Contradictory information (chunk A conflicts with chunk B)
- Stale content (doc references outdated info)
- Duplicate content (near-identical chunks from different sources)

**Graph nodes**: `Document`, `Topic`, `Entity`, `Author`
**Graph edges**: `CITES`, `SUPERSEDES`, `AUTHORED_BY`, `MENTIONS`, `RELATED_TO`

**Investigation agents**:
- Conflict Resolver: "These two docs say different things about X"
- Staleness Checker: "This doc references Y which was updated"
- Knowledge Mapper: "What do we know about topic Z"

---

## Vertical: SharePoint / File Events

**Adapter**: `SharePointAdapter` / `FileSystemAdapter`
- Source: Microsoft Graph webhooks, inotify, fswatch
- Events: file created, modified, deleted, moved, shared, permission changed

**Domain Processor**: `FileEventProcessor`
- Canonicalize: normalize paths, extract metadata
- Memory unit: file node with path, hash, permissions, timestamps

**Memory Store**:
- `files` (path, content_hash, size, modified_ts)
- `file_events` (event log of changes)
- `permissions` (who can access what)

**Detectors**:
- Mass deletion (ransomware pattern)
- Unusual access pattern (user accessing files they never touch)
- Sensitive content in public folder
- Permission escalation
- Large data movement (exfil pattern)

**Graph nodes**: `File`, `Folder`, `User`, `Group`, `Site`
**Graph edges**: `CONTAINS`, `CREATED_BY`, `MODIFIED_BY`, `SHARED_WITH`, `HAS_PERMISSION`

**Investigation agents**:
- Access Auditor: "Who touched this file and when"
- Blast Radius: "What's affected if this folder is compromised"
- Anomaly Explainer: "Why is this access pattern unusual"

---

## Vertical: Data Governance (Purview-style)

**Adapter**: `SchemaChangeAdapter` / `LineageAdapter`
- Source: database audit logs, schema change events, ETL job logs, CDC streams
- Events: column added, table dropped, ETL failed, data quality alert

**Domain Processor**: `GovernanceProcessor`
- Canonicalize: normalize schema references, extract lineage edges
- Memory unit: catalog entry with schema, lineage, classification

**Memory Store**:
- `data_assets` (tables, columns, datasets)
- `lineage_edges` (source → transform → destination)
- `classifications` (PII, sensitive, public)
- `quality_scores` (per asset)

**Detectors**:
- PII in unexpected location
- Breaking schema change (column type change, drop)
- ETL failure downstream of critical asset
- Data quality degradation
- Lineage break (upstream source disappeared)

**Graph nodes**: `Table`, `Column`, `Dataset`, `Pipeline`, `Job`
**Graph edges**: `CONTAINS`, `FEEDS_INTO`, `PRODUCED_BY`, `DEPENDS_ON`, `CLASSIFIED_AS`

**Investigation agents**:
- Impact Analyzer: "What downstream systems break if this table changes"
- Lineage Tracer: "Where did this data come from"
- Compliance Checker: "Is PII flowing where it shouldn't"

---

## Graph Strategy (FalkorDB)

The graph is the connective tissue across verticals. Key insight:

**Detection** = cheap, runs on relational/time-series data
**Diagnosis** = needs graph for causality traversal

### Universal Graph Patterns

Regardless of vertical, you're always asking:
- **Upstream**: What does this depend on?
- **Downstream**: What depends on this?
- **Blast radius**: If this fails, what's affected?
- **Common ancestor**: What connects these affected things?

### Cross-Vertical Connections

When multiple verticals run on the same platform:

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Service   │      │  Document   │      │    Table    │
│  (logs)     │      │  (docs)     │      │  (govern)   │
└──────┬──────┘      └──────┬──────┘      └──────┬──────┘
       │                    │                    │
       │    REFERENCES      │    DESCRIBES       │
       └────────────────────┴────────────────────┘
                            │
                     ┌──────┴──────┐
                     │   Entity    │
                     │ (shared)    │
                     └─────────────┘
```

Example: A document describes how an ETL pipeline works. The pipeline feeds a table. The table is used by an API service. Graph connects all of these.

---

## Implementation Sequence

### Now: Infrastructure Logs (vertical #1)
- Finish Phase 4A (incident pipeline)
- Battle-test detector → investigate loop
- Learn what's hard

### Next: Pick vertical #2
Candidates:
- **Document memory** — if the use case is "organizational knowledge"
- **File events** — if the use case is "security/compliance monitoring"
- **Data governance** — if the use case is "data platform observability"

Pick based on where you have a real problem to solve.

### Then: Extract shared engine
Once two verticals work, factor out:
- Adapter interface
- Detector loop engine
- Incident pipeline
- Context packer
- CrewAI orchestration wrapper
- Notification layer

The graph schema stays vertical-specific but the query patterns generalize.

---

## Open Questions

1. **Multi-tenant or single-tenant?** — Does each vertical get its own DB, or shared tables with a `vertical` column?

2. **Unified event bus?** — Should all adapters write to a shared event stream (Kafka-style) before processing, or direct to their processors?

3. **Cross-vertical correlation?** — When an API error coincides with an ETL failure, how do we connect those incidents across verticals?

4. **Embedding model per vertical?** — Logs vs documents vs code might need different embedding models. Or one model fits all?

5. **Agent reuse** — Some agents (Impact, Timeline) are universal. Others (Root Cause for logs vs Conflict Resolver for docs) are domain-specific. How much to share?

---

## Principles

1. **Cheap detection, expensive investigation** — Never run LLMs in the hot path. Detectors are SQL + stats.

2. **Own the graph before you infer it** — Start with `inventory.yaml` you maintain. Learn edges from observations only after manual truth exists.

3. **Bounded context** — Investigators work against context bundles, not the whole memory store.

4. **Adapters are dumb pipes** — They just normalize events. Business logic lives in processors and detectors.

5. **Ship one vertical end-to-end before generalizing** — Don't build the abstraction until you have two concrete implementations.
