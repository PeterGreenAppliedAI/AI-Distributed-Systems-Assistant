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
)
from db.database import get_pool
from services.embedding import embed_batch, embed_text
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


def compute_log_hash(log: LogEventCreate) -> str:
    """Compute a 16-char hex hash for deduplication."""
    content = f"{log.timestamp}|{log.host}|{log.service}|{log.message}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


router = APIRouter()


def _build_row(log: LogEventCreate, has_hash: bool, embedding: Optional[list[float]] = None, has_embedding: bool = False):
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
    return base


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

    # Build column list dynamically
    columns = []
    if has_hash_column:
        columns.append("log_hash")
    columns.extend([
        "timestamp", "source", "service", "host", "level",
        "trace_id", "span_id", "event_type", "error_code",
        "message", "meta_json",
    ])
    if has_embedding:
        columns.append("embedding_vector")

    ph = ["%s"] * len(columns)
    if has_embedding:
        ph[-1] = "VEC_FromText(%s)"  # embedding_vector is always last
    placeholders = ", ".join(ph)
    col_list = ", ".join(columns)
    ignore = "IGNORE " if has_hash_column else ""
    insert_sql = f"INSERT {ignore}INTO log_events ({col_list}) VALUES ({placeholders})"

    # Generate embeddings if column exists
    embeddings: list[Optional[list[float]]] = [None] * len(body.logs)
    if has_embedding:
        http_client = getattr(request.app.state, "http_client", None)
        if http_client:
            messages = [log.message for log in body.logs]
            try:
                embeddings = await embed_batch(http_client, messages)
            except Exception as e:
                logger.warning("Batch embedding failed, continuing without: %s", e)

    try:
        pool = get_pool()
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to get DB pool: {e}")

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                rows = [
                    _build_row(log, has_hash_column, emb, has_embedding)
                    for log, emb in zip(body.logs, embeddings)
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
