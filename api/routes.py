"""
DevMesh Platform - API Routes
Ingestion and query endpoints (async with connection pool)
"""

import json
import hashlib
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query, Request, status

from models.schemas import (
    LogEventCreate,
    LogEventResponse,
    LogIngestRequest,
    LogIngestResponse,
    LogLevel,
    LogSearchResult,
    TemplateSearchResult,
)
from db.database import get_pool
from services.embedding import embed_batch, embed_text
from services.canonicalize import template_key
from errors import EmptyBatchError, IngestionError, QueryError, DatabaseConnectionError

logger = logging.getLogger(__name__)


def _vec_to_text(vec: list[float]) -> str:
    """Convert a list of floats to MariaDB VECTOR text format."""
    return "[" + ",".join(str(f) for f in vec) + "]"


# =============================================================================
# Schema Cache
# =============================================================================
_has_hash_column: Optional[bool] = None
_has_embedding_column: Optional[bool] = None
_has_templates_table: Optional[bool] = None
_has_template_id_column: Optional[bool] = None


async def _check_hash_column_exists() -> bool:
    """Check if log_hash column exists (cached after first check)."""
    global _has_hash_column
    if _has_hash_column is not None:
        return _has_hash_column

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'log_events'
                      AND column_name = 'log_hash'
                """)
                row = await cursor.fetchone()
                _has_hash_column = row['cnt'] > 0
        logger.info("Schema check: log_hash column %s", 'exists' if _has_hash_column else 'missing')
    except Exception as e:
        logger.warning("Failed to check schema, assuming no hash column: %s", e)
        _has_hash_column = False

    return _has_hash_column


async def _check_embedding_column_exists() -> bool:
    """Check if embedding_vector column exists (cached after first check)."""
    global _has_embedding_column
    if _has_embedding_column is not None:
        return _has_embedding_column

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'log_events'
                      AND column_name = 'embedding_vector'
                """)
                row = await cursor.fetchone()
                _has_embedding_column = row['cnt'] > 0
        logger.info("Schema check: embedding_vector column %s",
                     'exists' if _has_embedding_column else 'missing')
    except Exception as e:
        logger.warning("Failed to check schema, assuming no embedding column: %s", e)
        _has_embedding_column = False

    return _has_embedding_column


async def _check_templates_table_exists() -> bool:
    """Check if log_templates table exists (cached after first check)."""
    global _has_templates_table
    if _has_templates_table is not None:
        return _has_templates_table

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name = 'log_templates'
                """)
                row = await cursor.fetchone()
                _has_templates_table = row['cnt'] > 0
        logger.info("Schema check: log_templates table %s",
                     'exists' if _has_templates_table else 'missing')
    except Exception as e:
        logger.warning("Failed to check schema, assuming no templates table: %s", e)
        _has_templates_table = False

    return _has_templates_table


async def _check_template_id_column_exists() -> bool:
    """Check if template_id column exists on log_events (cached)."""
    global _has_template_id_column
    if _has_template_id_column is not None:
        return _has_template_id_column

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'log_events'
                      AND column_name = 'template_id'
                """)
                row = await cursor.fetchone()
                _has_template_id_column = row['cnt'] > 0
        logger.info("Schema check: template_id column %s",
                     'exists' if _has_template_id_column else 'missing')
    except Exception as e:
        logger.warning("Failed to check schema, assuming no template_id column: %s", e)
        _has_template_id_column = False

    return _has_template_id_column


def compute_log_hash(log: LogEventCreate) -> str:
    """Compute a 16-char hex hash for deduplication."""
    content = f"{log.timestamp}|{log.host}|{log.service}|{log.message}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


router = APIRouter()


def _build_row(log: LogEventCreate, has_hash: bool, embedding: Optional[list[float]] = None,
               has_embedding: bool = False, template_id: Optional[int] = None,
               has_template_id: bool = False):
    """Build a parameter tuple for one log row."""
    meta_json_str = json.dumps(log.meta_json) if log.meta_json else None
    base = (
        log.timestamp,
        log.source,
        log.service,
        log.host,
        log.level.value,
        log.trace_id,
        log.span_id,
        log.event_type,
        log.error_code,
        log.message,
        meta_json_str,
    )
    if has_hash:
        base = (compute_log_hash(log),) + base
    if has_embedding:
        emb_blob = _vec_to_text(embedding) if embedding else None
        base = base + (emb_blob,)
    if has_template_id:
        base = base + (template_id,)
    return base


async def _resolve_templates(request: Request, pool, logs: list[LogEventCreate]):
    """Resolve template IDs for a batch of logs via canonicalization + cache + DB.

    Returns:
        list of template_id (int or None) for each log in the batch.
    """
    template_cache = getattr(request.app.state, "template_cache", None)
    if template_cache is None:
        return [None] * len(logs)

    # Step 1: Canonicalize each message, compute template_hash
    keys = []
    for log in logs:
        canonical, t_hash, version = template_key(
            log.message, log.service, log.level.value
        )
        keys.append((canonical, t_hash, version, log))

    # Step 2: Deduplicate within batch — collect unique hashes
    unique_hashes = {}
    for canonical, t_hash, version, log in keys:
        if t_hash not in unique_hashes:
            unique_hashes[t_hash] = (canonical, version, log)

    # Step 3: Cache lookup for each unique hash
    resolved: dict[str, int] = {}
    cache_misses: list[str] = []
    for t_hash in unique_hashes:
        tid = template_cache.get(t_hash)
        if tid is not None:
            resolved[t_hash] = tid
        else:
            cache_misses.append(t_hash)

    # Step 4: DB lookup for cache misses
    if cache_misses:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    placeholders = ", ".join(["%s"] * len(cache_misses))
                    await cursor.execute(
                        f"SELECT id, template_hash FROM log_templates WHERE template_hash IN ({placeholders})",
                        cache_misses,
                    )
                    rows = await cursor.fetchall()
                    for row in rows:
                        resolved[row['template_hash']] = row['id']
                        template_cache.put(row['template_hash'], row['id'])
                        cache_misses.remove(row['template_hash'])
        except Exception as e:
            logger.warning("Template DB lookup failed: %s", e)

    # Step 5: Embed only truly new canonical texts
    if cache_misses:
        http_client = getattr(request.app.state, "http_client", None)
        if http_client:
            new_texts = [unique_hashes[h][0] for h in cache_misses]
            try:
                new_embeddings = await embed_batch(http_client, new_texts)
            except Exception as e:
                logger.warning("Template embedding failed: %s", e)
                new_embeddings = [None] * len(new_texts)

            # Step 6: Insert new templates
            try:
                async with pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        for i, t_hash in enumerate(cache_misses):
                            canonical, version, log = unique_hashes[t_hash]
                            emb = new_embeddings[i] if i < len(new_embeddings) else None
                            if emb is None:
                                continue  # Can't insert without embedding (NOT NULL)

                            canon_hash_val = hashlib.sha256(canonical.encode()).hexdigest()[:32]
                            emb_text = _vec_to_text(emb)
                            now = log.timestamp

                            await cursor.execute("""
                                INSERT INTO log_templates
                                    (template_hash, canonical_text, service, level,
                                     embedding_vector, canon_version, canon_hash,
                                     first_seen, last_seen, event_count, source_hosts)
                                VALUES (%s, %s, %s, %s, VEC_FromText(%s), %s, %s,
                                        %s, %s, 1, %s)
                            """, (
                                t_hash, canonical, log.service, log.level.value,
                                emb_text, version, canon_hash_val,
                                now, now, json.dumps([log.host]),
                            ))
                            new_id = cursor.lastrowid
                            resolved[t_hash] = new_id
                            template_cache.put(t_hash, new_id)

                    await conn.commit()
            except Exception as e:
                logger.warning("Template insert failed: %s", e)

    # Step 8: Batch update event_count and last_seen on existing templates
    # (collect hashes that were already in DB, not newly created)
    existing_hashes = [
        (t_hash, keys_entry[3].timestamp)  # log.timestamp
        for keys_entry in keys
        for t_hash_check in [keys_entry[1]]
        if t_hash_check in resolved
        for t_hash in [t_hash_check]
    ]
    if existing_hashes:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # Count per template_hash for this batch
                    hash_counts: dict[str, tuple[int, datetime]] = {}
                    for t_hash, ts in existing_hashes:
                        if t_hash in hash_counts:
                            cnt, max_ts = hash_counts[t_hash]
                            hash_counts[t_hash] = (cnt + 1, max(max_ts, ts))
                        else:
                            hash_counts[t_hash] = (1, ts)

                    for t_hash, (cnt, max_ts) in hash_counts.items():
                        await cursor.execute("""
                            UPDATE log_templates
                            SET event_count = event_count + %s,
                                last_seen = GREATEST(last_seen, %s)
                            WHERE template_hash = %s
                        """, (cnt, max_ts, t_hash))
                await conn.commit()
        except Exception as e:
            logger.warning("Template counter update failed: %s", e)

    # Map back to per-log template IDs
    result = []
    for canonical, t_hash, version, log in keys:
        result.append(resolved.get(t_hash))

    return result


@router.post(
    "/ingest/logs",
    response_model=LogIngestResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Ingestion"],
)
async def ingest_logs(request: Request, body: LogIngestRequest):
    """Ingest batch of log events into MariaDB (async, bulk insert via executemany)."""
    if not body.logs:
        raise EmptyBatchError()

    has_hash_column = await _check_hash_column_exists()
    has_embedding = await _check_embedding_column_exists()
    has_templates = await _check_templates_table_exists()
    has_template_id = await _check_template_id_column_exists()

    try:
        pool = get_pool()
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to get DB pool: {e}")

    # Resolve templates if the table exists
    template_ids: list[Optional[int]] = [None] * len(body.logs)
    skip_per_row_embedding = False
    if has_templates and has_template_id:
        template_ids = await _resolve_templates(request, pool, body.logs)
        # If templates are active, skip per-row embedding on log_events
        skip_per_row_embedding = True

    # Build column list dynamically
    columns = []
    if has_hash_column:
        columns.append("log_hash")
    columns.extend([
        "timestamp", "source", "service", "host", "level",
        "trace_id", "span_id", "event_type", "error_code",
        "message", "meta_json",
    ])

    use_embedding = has_embedding and not skip_per_row_embedding
    if use_embedding:
        columns.append("embedding_vector")
    if has_template_id:
        columns.append("template_id")

    ph = ["%s"] * len(columns)
    if use_embedding:
        # Find embedding_vector position
        emb_idx = columns.index("embedding_vector")
        ph[emb_idx] = "VEC_FromText(%s)"
    placeholders = ", ".join(ph)
    col_list = ", ".join(columns)
    ignore = "IGNORE " if has_hash_column else ""
    insert_sql = f"INSERT {ignore}INTO log_events ({col_list}) VALUES ({placeholders})"

    # Generate embeddings if column exists and we're not using templates
    embeddings: list[Optional[list[float]]] = [None] * len(body.logs)
    if use_embedding:
        http_client = getattr(request.app.state, "http_client", None)
        if http_client:
            messages = [log.message for log in body.logs]
            try:
                embeddings = await embed_batch(http_client, messages)
            except Exception as e:
                logger.warning("Batch embedding failed, continuing without: %s", e)

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                rows = [
                    _build_row(log, has_hash_column, emb, use_embedding,
                               tid, has_template_id)
                    for log, emb, tid in zip(body.logs, embeddings, template_ids)
                ]

                await cursor.executemany(insert_sql, rows)

                ingested = cursor.rowcount
                duplicates = len(rows) - ingested if has_hash_column else 0

            await conn.commit()

        logger.info("Ingested %d logs (%d duplicates skipped)", ingested, duplicates)

        return LogIngestResponse(
            ingested=ingested,
            duplicates=duplicates,
            failed=0,
        )

    except (EmptyBatchError, DatabaseConnectionError):
        raise
    except Exception as e:
        logger.error("Batch ingestion failed: %s", e)
        raise IngestionError(
            message="Batch ingestion failed",
            ingested=0,
            failed=len(body.logs),
            errors=[str(e)],
        )


@router.get(
    "/query/logs",
    response_model=List[LogEventResponse],
    tags=["Query"],
)
async def query_logs(
    service: Optional[str] = Query(None, description="Filter by service name"),
    host: Optional[str] = Query(None, description="Filter by host name"),
    level: Optional[LogLevel] = Query(None, description="Filter by log level"),
    start_time: Optional[datetime] = Query(None, description="Start of time window (UTC)"),
    end_time: Optional[datetime] = Query(None, description="End of time window (UTC)"),
    limit: int = Query(100, ge=1, le=10000, description="Maximum logs to return"),
    offset: int = Query(0, ge=0, description="Number of logs to skip"),
):
    """Query log events with filters (async)."""
    try:
        pool = get_pool()
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to get DB pool: {e}")

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                where_clauses = []
                params: list = []

                if service:
                    where_clauses.append("service = %s")
                    params.append(service)
                if host:
                    where_clauses.append("host = %s")
                    params.append(host)
                if level:
                    where_clauses.append("level = %s")
                    params.append(level.value)
                if start_time:
                    where_clauses.append("timestamp >= %s")
                    params.append(start_time)
                if end_time:
                    where_clauses.append("timestamp <= %s")
                    params.append(end_time)

                where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
                query_sql = f"""
                SELECT id, timestamp, source, service, host, level,
                       trace_id, span_id, event_type, error_code,
                       message, meta_json
                FROM log_events
                WHERE {where_sql}
                ORDER BY timestamp DESC
                LIMIT %s OFFSET %s
                """
                params.extend([limit, offset])

                await cursor.execute(query_sql, params)
                rows = await cursor.fetchall()

                results = []
                for row in rows:
                    meta_json = json.loads(row['meta_json']) if row['meta_json'] else None
                    results.append(LogEventResponse(
                        id=row['id'],
                        timestamp=row['timestamp'],
                        source=row['source'],
                        service=row['service'],
                        host=row['host'],
                        level=LogLevel(row['level']),
                        trace_id=row['trace_id'],
                        span_id=row['span_id'],
                        event_type=row['event_type'],
                        error_code=row['error_code'],
                        message=row['message'],
                        meta_json=meta_json,
                    ))

                logger.info("Query returned %d logs (service=%s, host=%s, level=%s)",
                            len(results), service, host, level)
                return results

    except DatabaseConnectionError:
        raise
    except Exception as e:
        logger.error("Query failed: %s", e)
        raise QueryError("Log query failed")


@router.get(
    "/search/logs",
    response_model=List[LogSearchResult],
    tags=["Search"],
)
async def search_logs(
    request: Request,
    query: str = Query(..., description="Natural language search text"),
    host: Optional[str] = Query(None, description="Filter by host name"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    level: Optional[LogLevel] = Query(None, description="Filter by log level"),
    start_time: Optional[datetime] = Query(None, description="Start of time window (UTC)"),
    end_time: Optional[datetime] = Query(None, description="End of time window (UTC)"),
    limit: int = Query(20, ge=1, le=200, description="Maximum results to return"),
):
    """Semantic search over log events using vector similarity."""
    http_client = getattr(request.app.state, "http_client", None)
    if http_client is None:
        raise QueryError("Embedding client not available")

    query_embedding = await embed_text(http_client, query)
    if query_embedding is None:
        raise QueryError("Failed to generate query embedding")

    query_blob = _vec_to_text(query_embedding)

    try:
        pool = get_pool()
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to get DB pool: {e}")

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                where_clauses = ["embedding_vector IS NOT NULL"]
                params: list = []

                if host:
                    where_clauses.append("host = %s")
                    params.append(host)
                if service:
                    where_clauses.append("service = %s")
                    params.append(service)
                if level:
                    where_clauses.append("level = %s")
                    params.append(level.value)
                if start_time:
                    where_clauses.append("timestamp >= %s")
                    params.append(start_time)
                if end_time:
                    where_clauses.append("timestamp <= %s")
                    params.append(end_time)

                where_sql = " AND ".join(where_clauses)
                search_sql = f"""
                SELECT id, timestamp, source, service, host, level,
                       trace_id, span_id, event_type, error_code,
                       message, meta_json,
                       VEC_DISTANCE_COSINE(embedding_vector, VEC_FromText(%s)) as distance
                FROM log_events
                WHERE {where_sql}
                ORDER BY distance
                LIMIT %s
                """
                params.append(query_blob)
                params.append(limit)

                await cursor.execute(search_sql, params)
                rows = await cursor.fetchall()

                results = []
                for row in rows:
                    meta_json = json.loads(row['meta_json']) if row['meta_json'] else None
                    results.append(LogSearchResult(
                        id=row['id'],
                        timestamp=row['timestamp'],
                        source=row['source'],
                        service=row['service'],
                        host=row['host'],
                        level=LogLevel(row['level']),
                        trace_id=row['trace_id'],
                        span_id=row['span_id'],
                        event_type=row['event_type'],
                        error_code=row['error_code'],
                        message=row['message'],
                        meta_json=meta_json,
                        similarity_score=1.0 - float(row['distance']),
                    ))

                logger.info("Semantic search for '%s' returned %d results", query, len(results))
                return results

    except (DatabaseConnectionError, QueryError):
        raise
    except Exception as e:
        logger.error("Semantic search failed: %s", e)
        raise QueryError("Semantic search failed")


@router.get(
    "/search/templates",
    response_model=List[TemplateSearchResult],
    tags=["Search"],
)
async def search_templates(
    request: Request,
    query: str = Query(..., description="Natural language search text"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    level: Optional[LogLevel] = Query(None, description="Filter by log level"),
    limit: int = Query(10, ge=1, le=100, description="Maximum template results"),
    examples: int = Query(3, ge=0, le=20, description="Number of example events per template"),
):
    """Semantic search over canonicalized log templates.

    Two-step search:
    1. Vector search on log_templates (small table with HNSW index)
    2. Fetch recent raw examples from log_events for matched templates
    """
    http_client = getattr(request.app.state, "http_client", None)
    if http_client is None:
        raise QueryError("Embedding client not available")

    has_templates = await _check_templates_table_exists()
    if not has_templates:
        raise QueryError("log_templates table not available — run migration 003")

    # Canonicalize query text (no-op for natural language, helps for pasted log fragments)
    from services.canonicalize import canonicalize
    canon_query = canonicalize(query)

    query_embedding = await embed_text(http_client, canon_query)
    if query_embedding is None:
        raise QueryError("Failed to generate query embedding")

    query_blob = _vec_to_text(query_embedding)

    try:
        pool = get_pool()
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to get DB pool: {e}")

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # Step 1: Vector search on log_templates
                where_clauses = []
                params: list = []

                if service:
                    where_clauses.append("service = %s")
                    params.append(service)
                if level:
                    where_clauses.append("level = %s")
                    params.append(level.value)

                where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
                search_sql = f"""
                SELECT id, canonical_text, service, level, event_count,
                       VEC_DISTANCE_COSINE(embedding_vector, VEC_FromText(%s)) as distance
                FROM log_templates
                WHERE {where_sql}
                ORDER BY distance
                LIMIT %s
                """
                params.append(query_blob)
                params.append(limit)

                await cursor.execute(search_sql, params)
                template_rows = await cursor.fetchall()

                if not template_rows:
                    return []

                # Step 2: Fetch recent raw examples for matched templates
                template_ids = [row['id'] for row in template_rows]

                results = []
                for trow in template_rows:
                    example_events = []
                    if examples > 0:
                        await cursor.execute("""
                            SELECT id, timestamp, source, service, host, level,
                                   trace_id, span_id, event_type, error_code,
                                   message, meta_json
                            FROM log_events
                            WHERE template_id = %s
                            ORDER BY timestamp DESC
                            LIMIT %s
                        """, (trow['id'], examples))
                        event_rows = await cursor.fetchall()

                        for erow in event_rows:
                            meta_json = json.loads(erow['meta_json']) if erow['meta_json'] else None
                            example_events.append(LogEventResponse(
                                id=erow['id'],
                                timestamp=erow['timestamp'],
                                source=erow['source'],
                                service=erow['service'],
                                host=erow['host'],
                                level=LogLevel(erow['level']),
                                trace_id=erow['trace_id'],
                                span_id=erow['span_id'],
                                event_type=erow['event_type'],
                                error_code=erow['error_code'],
                                message=erow['message'],
                                meta_json=meta_json,
                            ))

                    results.append(TemplateSearchResult(
                        template_id=trow['id'],
                        canonical_text=trow['canonical_text'],
                        service=trow['service'],
                        level=trow['level'],
                        event_count=trow['event_count'],
                        similarity_score=1.0 - float(trow['distance']),
                        example_events=example_events,
                    ))

                logger.info("Template search for '%s' returned %d templates", query, len(results))
                return results

    except (DatabaseConnectionError, QueryError):
        raise
    except Exception as e:
        logger.error("Template search failed: %s", e)
        raise QueryError("Template search failed")
