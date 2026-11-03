"""
DevMesh Platform - LLM Tool Definitions
Five tools the LLM can call during analysis: search_templates, query_logs,
get_template_stats, get_service_overview, get_log_context.

Each tool has:
- An Ollama-compatible JSON schema (for tool calling)
- An async execution function that queries MariaDB
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from services.embedding import embed_text

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 4000


def _vec_to_text(vec: list[float]) -> str:
    """Convert a list of floats to MariaDB VECTOR text format."""
    return "[" + ",".join(str(f) for f in vec) + "]"


# Bounds for LLM-supplied arguments. Tool args come from the model, which
# reads attacker-influenced log text, so never trust them unclamped.
MAX_LIMIT = 200
MAX_HOURS = 720
MAX_WINDOW_MINUTES = 120


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    """Coerce an LLM-supplied value to int and clamp it to [lo, hi]."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def truncate_result(result: list[dict], max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """JSON-serialize a result list, dropping whole trailing items if too long.

    Always returns valid JSON so downstream parsing (_extract_sources) works.
    When items are dropped, a final {"_truncated": ...} marker item is appended.
    """
    text = json.dumps(result, default=str)
    if len(text) <= max_chars or not isinstance(result, list):
        return text
    total = len(result)
    kept: list = []
    for item in result:
        candidate = kept + [item, {"_truncated": True, "shown": len(kept) + 1, "total": total}]
        if len(json.dumps(candidate, default=str)) > max_chars:
            break
        kept.append(item)
    kept.append({"_truncated": True, "shown": len(kept), "total": total,
                 "note": f"truncated, {total} total results"})
    return json.dumps(kept, default=str)


# =============================================================================
# Tool Schemas (Ollama format)
# =============================================================================

SEARCH_TEMPLATES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_templates",
        "description": "Semantic search on canonical log templates. Use this to find log patterns relevant to a topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text",
                },
                "service": {
                    "type": "string",
                    "description": "Filter by service name (optional)",
                },
                "level": {
                    "type": "string",
                    "description": "Filter by log level: DEBUG, INFO, WARN, ERROR, CRITICAL (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 5)",
                },
            },
            "required": ["query"],
        },
    },
}

QUERY_LOGS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_logs",
        "description": "Retrieve raw log events with filters. Use this for specific time/host/service/level queries.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Filter by service name (optional)",
                },
                "host": {
                    "type": "string",
                    "description": "Filter by host name (optional)",
                },
                "level": {
                    "type": "string",
                    "description": "Filter by log level (optional)",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start of time window, ISO 8601 format (optional)",
                },
                "end_time": {
                    "type": "string",
                    "description": "End of time window, ISO 8601 format (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20)",
                },
            },
            "required": [],
        },
    },
}

GET_TEMPLATE_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_template_stats",
        "description": "Get template frequency over a time window. Use this to detect anomalies or frequency spikes.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Filter by service name (optional)",
                },
                "hours": {
                    "type": "integer",
                    "description": "Lookback window in hours (default 24)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum templates to return (default 20)",
                },
            },
            "required": [],
        },
    },
}

GET_SERVICE_OVERVIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_service_overview",
        "description": "High-level summary of all services: event counts, error counts, active hosts. Start here for situational awareness.",
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Lookback window in hours (default 24)",
                },
            },
            "required": [],
        },
    },
}

GET_LOG_CONTEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_log_context",
        "description": "Get surrounding log events for a specific event ID. Use this to understand what happened before and after an event.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The log event ID to get context for",
                },
                "window_minutes": {
                    "type": "integer",
                    "description": "Time window in minutes around the event (default 5)",
                },
            },
            "required": ["event_id"],
        },
    },
}

ALL_TOOL_SCHEMAS = [
    SEARCH_TEMPLATES_SCHEMA,
    QUERY_LOGS_SCHEMA,
    GET_TEMPLATE_STATS_SCHEMA,
    GET_SERVICE_OVERVIEW_SCHEMA,
    GET_LOG_CONTEXT_SCHEMA,
]


# =============================================================================
# Tool Execution Functions
# =============================================================================

async def execute_search_templates(
    pool, http_client: httpx.AsyncClient, args: dict
) -> list[dict]:
    """Semantic search on log_templates via vector similarity."""
    query = args.get("query", "")
    service = args.get("service")
    level = args.get("level")
    limit = _clamp_int(args.get("limit"), 5, 1, MAX_LIMIT)

    query_embedding = await embed_text(http_client, query)
    if query_embedding is None:
        return [{"error": "Failed to generate query embedding"}]

    query_blob = _vec_to_text(query_embedding)

    where_clauses = []
    params: list[Any] = []

    if service:
        where_clauses.append("service = %s")
        params.append(service)
    if level:
        where_clauses.append("level = %s")
        params.append(level)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    sql = f"""
        SELECT id, canonical_text, service, level, event_count,
               VEC_DISTANCE_COSINE(embedding_vector, VEC_FromText(%s)) as distance
        FROM log_templates
        WHERE {where_sql}
        ORDER BY distance
        LIMIT %s
    """
    # SELECT's VEC_FromText(%s) precedes the WHERE placeholders
    params = [query_blob] + params + [limit]

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, params)
            rows = await cursor.fetchall()

    return [
        {
            "template_id": row["id"],
            "canonical_text": row["canonical_text"],
            "service": row["service"],
            "level": row["level"],
            "event_count": row["event_count"],
            "similarity": round(1.0 - float(row["distance"]), 4),
        }
        for row in rows
    ]


async def execute_query_logs(pool, args: dict) -> list[dict]:
    """Filtered raw log retrieval from log_events."""
    service = args.get("service")
    host = args.get("host")
    level = args.get("level")
    start_time = args.get("start_time")
    end_time = args.get("end_time")
    limit = _clamp_int(args.get("limit"), 20, 1, MAX_LIMIT)

    where_clauses = []
    params: list[Any] = []

    if service:
        where_clauses.append("service = %s")
        params.append(service)
    if host:
        where_clauses.append("host = %s")
        params.append(host)
    if level:
        where_clauses.append("level = %s")
        params.append(level)
    if start_time:
        where_clauses.append("timestamp >= %s")
        params.append(start_time)
    if end_time:
        where_clauses.append("timestamp <= %s")
        params.append(end_time)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    sql = f"""
        SELECT id, timestamp, service, host, level, message
        FROM log_events
        WHERE {where_sql}
        ORDER BY timestamp DESC
        LIMIT %s
    """
    params.append(limit)

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, params)
            rows = await cursor.fetchall()

    return [
        {
            "id": row["id"],
            "timestamp": str(row["timestamp"]),
            "service": row["service"],
            "host": row["host"],
            "level": row["level"],
            "message": row["message"],
        }
        for row in rows
    ]


async def execute_get_template_stats(pool, args: dict) -> list[dict]:
    """Template frequency over a time window."""
    service = args.get("service")
    hours = _clamp_int(args.get("hours"), 24, 1, MAX_HOURS)
    limit = _clamp_int(args.get("limit"), 20, 1, MAX_LIMIT)

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    where_clauses = ["e.timestamp >= %s"]
    params: list[Any] = [cutoff]

    if service:
        where_clauses.append("t.service = %s")
        params.append(service)

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT t.id as template_id, t.canonical_text, t.service, t.level,
               COUNT(e.id) as recent_count, t.event_count as total_count
        FROM log_templates t
        JOIN log_events e ON e.template_id = t.id
        WHERE {where_sql}
        GROUP BY t.id, t.canonical_text, t.service, t.level, t.event_count
        ORDER BY recent_count DESC
        LIMIT %s
    """
    params.append(limit)

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, params)
            rows = await cursor.fetchall()

    return [
        {
            "template_id": row["template_id"],
            "canonical_text": row["canonical_text"],
            "service": row["service"],
            "level": row["level"],
            "recent_count": row["recent_count"],
            "total_count": row["total_count"],
        }
        for row in rows
    ]


async def execute_get_service_overview(pool, args: dict) -> list[dict]:
    """High-level summary of all services."""
    hours = _clamp_int(args.get("hours"), 24, 1, MAX_HOURS)
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    sql = """
        SELECT service,
               COUNT(*) as total_events,
               SUM(CASE WHEN level IN ('ERROR', 'CRITICAL', 'FATAL') THEN 1 ELSE 0 END) as error_count,
               COUNT(DISTINCT host) as active_hosts,
               MIN(timestamp) as first_event,
               MAX(timestamp) as last_event
        FROM log_events
        WHERE timestamp >= %s
        GROUP BY service
        ORDER BY total_events DESC
    """

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, [cutoff])
            rows = await cursor.fetchall()

    return [
        {
            "service": row["service"],
            "total_events": row["total_events"],
            "error_count": row["error_count"],
            "active_hosts": row["active_hosts"],
            "first_event": str(row["first_event"]),
            "last_event": str(row["last_event"]),
        }
        for row in rows
    ]


async def execute_get_log_context(pool, args: dict) -> list[dict]:
    """Surrounding logs for a specific event (temporal neighborhood)."""
    event_id = args.get("event_id")
    window_minutes = _clamp_int(args.get("window_minutes"), 5, 1, MAX_WINDOW_MINUTES)

    if event_id is None:
        return [{"error": "event_id is required"}]

    # Fetch the target event
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, timestamp, host FROM log_events WHERE id = %s",
                [event_id],
            )
            target = await cursor.fetchone()

            if not target:
                return [{"error": f"Event {event_id} not found"}]

            ts = target["timestamp"]
            host = target["host"]
            window = timedelta(minutes=window_minutes)

            await cursor.execute(
                """
                SELECT id, timestamp, service, host, level, message
                FROM log_events
                WHERE host = %s
                  AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp
                LIMIT 50
                """,
                [host, ts - window, ts + window],
            )
            rows = await cursor.fetchall()

    return [
        {
            "id": row["id"],
            "timestamp": str(row["timestamp"]),
            "service": row["service"],
            "host": row["host"],
            "level": row["level"],
            "message": row["message"],
        }
        for row in rows
    ]


# =============================================================================
# Tool Dispatcher
# =============================================================================

TOOL_EXECUTORS = {
    "search_templates": lambda pool, http_client, args: execute_search_templates(pool, http_client, args),
    "query_logs": lambda pool, http_client, args: execute_query_logs(pool, args),
    "get_template_stats": lambda pool, http_client, args: execute_get_template_stats(pool, args),
    "get_service_overview": lambda pool, http_client, args: execute_get_service_overview(pool, args),
    "get_log_context": lambda pool, http_client, args: execute_get_log_context(pool, args),
}


async def execute_tool(
    pool, http_client: httpx.AsyncClient, tool_name: str, arguments: dict
) -> str:
    """Execute a tool by name and return JSON string result (truncated if needed)."""
    executor = TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        return json.dumps([{"error": f"Unknown tool: {tool_name}"}])

    try:
        result = await executor(pool, http_client, arguments)
        return truncate_result(result)
    except Exception as e:
        logger.error("Tool %s execution failed: %s", tool_name, e)
        return json.dumps([{"error": f"Tool execution failed: {str(e)}"}])
