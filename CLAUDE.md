# Claude Code — DevMesh Platform Project Instructions

## Before Starting Work

1. Read `NEXT_STEPS.md` for current project status, active phase, and immediate TODOs
2. Read `ARCHITECTURE.md` for system components, data flow, and tech decisions
3. Check what phase is active — don't build ahead of the current phase

## When Completing Work

1. Update `NEXT_STEPS.md` with any status changes (check/uncheck items, add new TODOs)
2. If architecture changed (new components, new tables, new endpoints), update `ARCHITECTURE.md`
3. If performance observations were made (benchmarks, bottlenecks, thermal data), add to the observations section in `NEXT_STEPS.md`

## Project Context

- **Platform**: AI-native observability for local infrastructure (7 Linux nodes)
- **Database**: MariaDB 12.0.2 at 10.0.0.18 with native VECTOR type
- **LLM Gateway**: Ollama at 192.168.1.184:8001 
- **Graph DB**: FalkorDB (planned, not yet deployed)
- **All inference is local** — no external API costs, iterate freely

## Key Lessons Learned

- MariaDB VECTOR columns need `VEC_FromText(%s)` in SQL — raw text/binary assignment fails via pymysql
- MariaDB requires NOT NULL for HNSW vector indexes — add column nullable first, index after backfill
- `WHERE column IS NULL ORDER BY id` degrades to full table scan as NULLs decrease — use ID-based cursor tracking instead
- Ollama `/v1/embeddings` (OpenAI-compatible) supports batch input and is ~30x faster than sequential `/api/embeddings`
- DGX Spark thermals: 19 rows/s hits 80C, 2s inter-batch delay caps at 70C
- **Embedding versioning must be in the schema before any canonicalization work** — unversioned embeddings are the real cost, not re-embedding itself
- **Canonicalize before embedding** — raw systemd logs with PIDs, IPs, hex tokens destroy similarity clustering
- **Measure compression ratios** before assuming dedup savings — sample and count, don't guess

## Codebase Notes

- Tests: `python3 -m pytest tests/` (78 tests)
- Schema cache in `api/routes.py`: `_has_hash_column`, `_has_embedding_column`, `_has_templates_table`, `_has_template_id_column` are module-level globals, reset them in test fixtures
- `ingest_logs()` takes `request: Request` as first param (for `app.state.http_client` access) — FastAPI injects this automatically, doesn't affect test client calls
- Sync DB access: `db.database.get_sync_connection()` / `get_connection()` — used by migrations and CLI scripts
- Async DB access: `db.database.get_pool()` — used by API routes
- Template cache: `app.state.template_cache` is a `TemplateCache` instance, warmed at startup from `log_templates`
- Canonicalization: `services/canonicalize.py` — pure functions, versioned rules (v1), no I/O
- Template dedup: `log_templates` table stores unique canonical templates with embeddings; `log_events.template_id` links to it
- When `log_templates` exists, ingest embeds only new canonical texts (not every raw log); search via `/search/templates`
- Backfill templates: `python3 scripts/backfill_templates.py --batch-size 50 --delay 2`
- Safety net cron: `python3 scripts/cron_template_safety_net.py --batch-size 100 --delay 2`
