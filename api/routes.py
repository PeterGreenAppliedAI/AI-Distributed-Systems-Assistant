"""
DevMesh Platform - API Routes
Ingestion and query endpoints (async with connection pool)
"""

import json
import hashlib
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query, status

from models.schemas import (
    LogEventCreate,
    LogEventResponse,
    LogIngestRequest,
    LogIngestResponse,
    LogLevel,
)
from db.database import get_pool
from errors import EmptyBatchError, IngestionError, QueryError, DatabaseConnectionError

logger = logging.getLogger(__name__)

# =============================================================================
# Schema Cache
# =============================================================================
_has_hash_column: Optional[bool] = None


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


def compute_log_hash(log: LogEventCreate) -> str:
    """Compute a 16-char hex hash for deduplication."""
    content = f"{log.timestamp}|{log.host}|{log.service}|{log.message}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


router = APIRouter()


def _build_row(log: LogEventCreate, has_hash: bool):
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
        return (compute_log_hash(log),) + base
    return base


@router.post(
    "/ingest/logs",
    response_model=LogIngestResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Ingestion"],
)
async def ingest_logs(request: LogIngestRequest):
    """Ingest batch of log events into MariaDB (async, bulk insert via executemany)."""
    if not request.logs:
        raise EmptyBatchError()

    has_hash_column = await _check_hash_column_exists()

    if has_hash_column:
        insert_sql = """
        INSERT IGNORE INTO log_events (
            log_hash, timestamp, source, service, host, level,
            trace_id, span_id, event_type, error_code,
            message, meta_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
    else:
        insert_sql = """
        INSERT INTO log_events (
            timestamp, source, service, host, level,
            trace_id, span_id, event_type, error_code,
            message, meta_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

    try:
        pool = get_pool()
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to get DB pool: {e}")

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # Build all rows
                rows = [_build_row(log, has_hash_column) for log in request.logs]

                # Bulk insert (M2)
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
            failed=len(request.logs),
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
