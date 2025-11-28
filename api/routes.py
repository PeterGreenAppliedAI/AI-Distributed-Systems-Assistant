"""
DevMesh Platform - API Routes
Ingestion and query endpoints
"""

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
import pymysql

from models.schemas import (
    LogEventCreate,
    LogEventResponse,
    LogIngestRequest,
    LogIngestResponse,
    LogQueryParams,
    LogLevel
)
from db.database import get_connection

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()


@router.post(
    "/ingest/logs",
    response_model=LogIngestResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Ingestion"]
)
async def ingest_logs(request: LogIngestRequest):
    """
    Ingest batch of log events into MariaDB.

    This endpoint accepts a batch of log events and stores them in the log_events table.
    Logs are inserted in a single transaction for efficiency.

    **Parameters:**
    - logs: List of log event objects

    **Returns:**
    - ingested: Number of logs successfully inserted
    - failed: Number of logs that failed
    - errors: List of error messages (if any)
    """
    if not request.logs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No logs provided in request"
        )

    conn = get_connection()
    ingested = 0
    failed = 0
    errors = []

    try:
        with conn.cursor() as cursor:
            # Prepare INSERT statement
            insert_sql = """
            INSERT INTO log_events (
                timestamp, source, service, host, level,
                trace_id, span_id, event_type, error_code,
                message, meta_json
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            """

            # Insert each log
            for log in request.logs:
                try:
                    # Convert meta_json dict to JSON string if present
                    meta_json_str = json.dumps(log.meta_json) if log.meta_json else None

                    cursor.execute(insert_sql, (
                        log.timestamp,
                        log.source,
                        log.service,
                        log.host,
                        log.level.value,  # Convert enum to string
                        log.trace_id,
                        log.span_id,
                        log.event_type,
                        log.error_code,
                        log.message,
                        meta_json_str
                    ))
                    ingested += 1

                except Exception as e:
                    failed += 1
                    error_msg = f"Failed to insert log: {str(e)}"
                    errors.append(error_msg)
                    logger.error(error_msg)

            # Commit transaction
            conn.commit()
            logger.info(f"✓ Ingested {ingested} logs ({failed} failed)")

    except Exception as e:
        conn.rollback()
        logger.error(f"✗ Batch ingestion failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch ingestion failed: {str(e)}"
        )
    finally:
        conn.close()

    return LogIngestResponse(
        ingested=ingested,
        failed=failed,
        errors=errors if errors else None
    )


@router.get(
    "/query/logs",
    response_model=List[LogEventResponse],
    tags=["Query"]
)
async def query_logs(
    service: Optional[str] = Query(None, description="Filter by service name"),
    host: Optional[str] = Query(None, description="Filter by host name"),
    level: Optional[LogLevel] = Query(None, description="Filter by log level"),
    start_time: Optional[datetime] = Query(None, description="Start of time window (UTC)"),
    end_time: Optional[datetime] = Query(None, description="End of time window (UTC)"),
    limit: int = Query(100, ge=1, le=10000, description="Maximum logs to return"),
    offset: int = Query(0, ge=0, description="Number of logs to skip")
):
    """
    Query log events with filters.

    **Filters:**
    - service: Exact service name match
    - host: Exact host name match
    - level: Log level (DEBUG, INFO, WARN, ERROR, etc.)
    - start_time: Beginning of time window (UTC)
    - end_time: End of time window (UTC)
    - limit: Max results to return (default 100, max 10000)
    - offset: Number of results to skip for pagination

    **Returns:**
    - List of matching log events, ordered by timestamp DESC
    """
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            # Build WHERE clause dynamically
            where_clauses = []
            params = []

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

            # Construct SQL query
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            query_sql = f"""
            SELECT
                id, timestamp, source, service, host, level,
                trace_id, span_id, event_type, error_code,
                message, meta_json
            FROM log_events
            WHERE {where_sql}
            ORDER BY timestamp DESC
            LIMIT %s OFFSET %s
            """

            params.extend([limit, offset])

            # Execute query
            cursor.execute(query_sql, params)
            rows = cursor.fetchall()

            # Convert rows to LogEventResponse objects
            results = []
            for row in rows:
                # Parse meta_json string back to dict
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
                    meta_json=meta_json
                ))

            logger.info(f"✓ Query returned {len(results)} logs (service={service}, host={host}, level={level})")
            return results

    except Exception as e:
        logger.error(f"✗ Query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )
    finally:
        conn.close()
