"""
DevMesh Platform - LLM Reasoning Layer Tests
Tests for tool execution, orchestration loop, and /analyze endpoint.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient, ASGITransport

# Ensure test env vars are set before app import
os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "devmesh_test")
os.environ.setdefault("API_AUTH_ENABLED", "false")
os.environ.setdefault("API_KEY", "test-api-key-12345")
os.environ.setdefault("NODE_NAME", "test-node")

from tests.conftest import MockPool, MockConnection, MockCursor


# =============================================================================
# Helpers: Mock pool with configurable results
# =============================================================================

class ConfigurableCursor:
    """Cursor that returns pre-configured results for specific queries."""

    def __init__(self, results_map=None, default_results=None):
        self.results_map = results_map or {}
        self.default_results = default_results or []
        self._current_results = []
        self._executed = []
        self.rowcount = 0
        self.lastrowid = 1

    async def execute(self, sql, params=None):
        self._executed.append((sql, params))
        self._current_results = self.default_results
        # Check results_map for keyword matches
        for keyword, results in self.results_map.items():
            if keyword in sql:
                self._current_results = results
                break
        self.rowcount = len(self._current_results)

    async def executemany(self, sql, rows):
        self._executed.append((sql, rows))
        self.rowcount = len(rows)

    async def fetchone(self):
        return self._current_results[0] if self._current_results else None

    async def fetchall(self):
        return self._current_results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class ConfigurableConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class ConfigurablePool:
    def __init__(self, cursor):
        self._conn = ConfigurableConnection(cursor)

    def acquire(self):
        return self._conn

    def close(self):
        pass

    async def wait_closed(self):
        pass

    @property
    def minsize(self):
        return 2

    @property
    def maxsize(self):
        return 10


# =============================================================================
# Tool Execution Tests
# =============================================================================

class TestToolExecution:
    """Tests for individual LLM tool functions."""

    @pytest.mark.asyncio
    async def test_execute_query_logs(self):
        """query_logs builds correct SQL and returns structured results."""
        from services.llm_tools import execute_query_logs

        cursor = ConfigurableCursor(
            results_map={
                "FROM log_events": [
                    {
                        "id": 1,
                        "timestamp": datetime(2026, 1, 15, 10, 0, 0),
                        "service": "nginx.service",
                        "host": "dev-services",
                        "level": "ERROR",
                        "message": "connection refused",
                    }
                ]
            }
        )
        pool = ConfigurablePool(cursor)

        result = await execute_query_logs(pool, {
            "host": "dev-services",
            "level": "ERROR",
            "limit": 5,
        })

        assert len(result) == 1
        assert result[0]["host"] == "dev-services"
        assert result[0]["level"] == "ERROR"
        assert result[0]["message"] == "connection refused"

        # Verify SQL was constructed with filters
        sql, params = cursor._executed[0]
        assert "host = %s" in sql
        assert "level = %s" in sql
        assert "dev-services" in params
        assert "ERROR" in params

    @pytest.mark.asyncio
    async def test_execute_query_logs_no_filters(self):
        """query_logs with no filters returns all recent logs."""
        from services.llm_tools import execute_query_logs

        cursor = ConfigurableCursor(
            results_map={
                "FROM log_events": [
                    {
                        "id": 1,
                        "timestamp": datetime(2026, 1, 15, 10, 0, 0),
                        "service": "test.service",
                        "host": "test-node",
                        "level": "INFO",
                        "message": "test message",
                    }
                ]
            }
        )
        pool = ConfigurablePool(cursor)

        result = await execute_query_logs(pool, {})
        assert len(result) == 1

        sql, params = cursor._executed[0]
        assert "1=1" in sql

    @pytest.mark.asyncio
    async def test_execute_get_service_overview(self):
        """get_service_overview returns aggregated stats per service."""
        from services.llm_tools import execute_get_service_overview

        cursor = ConfigurableCursor(
            results_map={
                "GROUP BY service": [
                    {
                        "service": "nginx.service",
                        "total_events": 1500,
                        "error_count": 23,
                        "active_hosts": 3,
                        "first_event": datetime(2026, 1, 15, 0, 0),
                        "last_event": datetime(2026, 1, 15, 23, 59),
                    },
                    {
                        "service": "docker.service",
                        "total_events": 800,
                        "error_count": 5,
                        "active_hosts": 2,
                        "first_event": datetime(2026, 1, 15, 1, 0),
                        "last_event": datetime(2026, 1, 15, 22, 0),
                    },
                ]
            }
        )
        pool = ConfigurablePool(cursor)

        result = await execute_get_service_overview(pool, {"hours": 24})
        assert len(result) == 2
        assert result[0]["service"] == "nginx.service"
        assert result[0]["error_count"] == 23
        assert result[1]["service"] == "docker.service"

    @pytest.mark.asyncio
    async def test_execute_get_template_stats(self):
        """get_template_stats returns template frequency data."""
        from services.llm_tools import execute_get_template_stats

        cursor = ConfigurableCursor(
            results_map={
                "GROUP BY t.id": [
                    {
                        "template_id": 42,
                        "canonical_text": "connection refused to <IP>",
                        "service": "nginx.service",
                        "level": "ERROR",
                        "recent_count": 150,
                        "total_count": 500,
                    }
                ]
            }
        )
        pool = ConfigurablePool(cursor)

        result = await execute_get_template_stats(pool, {"service": "nginx.service"})
        assert len(result) == 1
        assert result[0]["recent_count"] == 150

    @pytest.mark.asyncio
    async def test_execute_get_log_context(self):
        """get_log_context returns surrounding events."""
        from services.llm_tools import execute_get_log_context

        target_event = {
            "id": 100,
            "timestamp": datetime(2026, 1, 15, 10, 30, 0),
            "host": "gpu-node",
        }
        context_events = [
            {
                "id": 99,
                "timestamp": datetime(2026, 1, 15, 10, 28, 0),
                "service": "docker.service",
                "host": "gpu-node",
                "level": "INFO",
                "message": "container starting",
            },
            {
                "id": 100,
                "timestamp": datetime(2026, 1, 15, 10, 30, 0),
                "service": "docker.service",
                "host": "gpu-node",
                "level": "ERROR",
                "message": "container crashed",
            },
        ]

        # Cursor returns target event on first query, context on second
        call_count = 0
        original_execute = ConfigurableCursor.execute

        cursor = ConfigurableCursor()

        async def custom_execute(self, sql, params=None):
            nonlocal call_count
            self._executed.append((sql, params))
            if call_count == 0:
                self._current_results = [target_event]
            else:
                self._current_results = context_events
            self.rowcount = len(self._current_results)
            call_count += 1

        cursor.execute = lambda sql, params=None: custom_execute(cursor, sql, params)
        pool = ConfigurablePool(cursor)

        result = await execute_get_log_context(pool, {"event_id": 100})
        assert len(result) == 2
        assert result[0]["id"] == 99

    @pytest.mark.asyncio
    async def test_execute_get_log_context_missing_event(self):
        """get_log_context with invalid event_id returns error."""
        from services.llm_tools import execute_get_log_context

        cursor = ConfigurableCursor(default_results=[])
        pool = ConfigurablePool(cursor)

        result = await execute_get_log_context(pool, {"event_id": 99999})
        assert len(result) == 1
        assert "error" in result[0]

    @pytest.mark.asyncio
    async def test_execute_get_log_context_no_event_id(self):
        """get_log_context without event_id returns error."""
        from services.llm_tools import execute_get_log_context

        cursor = ConfigurableCursor()
        pool = ConfigurablePool(cursor)

        result = await execute_get_log_context(pool, {})
        assert result[0]["error"] == "event_id is required"

    @pytest.mark.asyncio
    async def test_execute_search_templates(self):
        """search_templates embeds query and runs vector search."""
        from services.llm_tools import execute_search_templates

        cursor = ConfigurableCursor(
            results_map={
                "VEC_DISTANCE_COSINE": [
                    {
                        "id": 10,
                        "canonical_text": "connection refused to <IP>",
                        "service": "nginx.service",
                        "level": "ERROR",
                        "event_count": 200,
                        "distance": 0.15,
                    }
                ]
            }
        )
        pool = ConfigurablePool(cursor)

        mock_client = AsyncMock()
        with patch("services.llm_tools.embed_text", return_value=[0.1] * 4096):
            result = await execute_search_templates(pool, mock_client, {"query": "connection error"})

        assert len(result) == 1
        assert result[0]["similarity"] == 0.85
        assert result[0]["canonical_text"] == "connection refused to <IP>"

    @pytest.mark.asyncio
    async def test_execute_search_templates_embedding_failure(self):
        """search_templates returns error when embedding fails."""
        from services.llm_tools import execute_search_templates

        pool = ConfigurablePool(ConfigurableCursor())
        mock_client = AsyncMock()

        with patch("services.llm_tools.embed_text", return_value=None):
            result = await execute_search_templates(pool, mock_client, {"query": "test"})

        assert result[0]["error"] == "Failed to generate query embedding"


class TestToolDispatcher:
    """Tests for the tool dispatcher."""

    @pytest.mark.asyncio
    async def test_execute_tool_unknown(self):
        """Unknown tool name returns error JSON."""
        from services.llm_tools import execute_tool

        result = await execute_tool(None, None, "nonexistent_tool", {})
        parsed = json.loads(result)
        assert parsed[0]["error"] == "Unknown tool: nonexistent_tool"

    @pytest.mark.asyncio
    async def test_execute_tool_exception_handling(self):
        """Tool execution exceptions are caught and returned as error JSON."""
        from services.llm_tools import execute_tool

        # Pass None pool to trigger an exception in query_logs
        result = await execute_tool(None, None, "query_logs", {})
        parsed = json.loads(result)
        assert "error" in parsed[0]


class TestTruncation:
    """Tests for tool result truncation."""

    def test_truncate_short_result(self):
        """Short results are not truncated."""
        from services.llm_tools import truncate_result

        data = [{"id": 1, "message": "test"}]
        result = truncate_result(data)
        assert result == json.dumps(data, default=str)

    def test_truncate_long_result(self):
        """Long results are truncated with count."""
        from services.llm_tools import truncate_result

        data = [{"id": i, "message": "x" * 500} for i in range(20)]
        result = truncate_result(data, max_chars=100)
        assert "truncated" in result
        assert "20 total results" in result


# =============================================================================
# Orchestration Loop Tests
# =============================================================================

class TestOrchestration:
    """Tests for the LLM orchestration loop."""

    @pytest.mark.asyncio
    async def test_single_turn_no_tools(self):
        """LLM answers directly without calling any tools."""
        from services.llm import analyze

        mock_ollama_response = {
            "message": {
                "role": "assistant",
                "content": "Based on the available data, there are no significant errors. Confidence: high",
            },
            "done": True,
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value=mock_ollama_response),
        ))

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm.LLM_MODEL", "test-model"), \
             patch("services.llm._prefetch_context", return_value=[]):
            result = await analyze(mock_client, pool, "Are there any errors?")

        assert result.explanation == "Based on the available data, there are no significant errors. Confidence: high"
        assert result.confidence == "high"
        assert result.iterations == 1
        assert len(result.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_one_tool_call(self):
        """LLM calls one tool, gets result, then answers."""
        from services.llm import analyze

        # First response: tool call
        tool_call_response = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_service_overview",
                            "arguments": {"hours": 24},
                        }
                    }
                ],
            },
            "done": True,
        }

        # Second response: final answer
        final_response = {
            "message": {
                "role": "assistant",
                "content": "The nginx service has 23 errors in the last 24 hours. Confidence: medium",
            },
            "done": True,
        }

        call_count = 0

        def make_response(resp_data):
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.json = MagicMock(return_value=resp_data)
            return mock

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(tool_call_response)
            return make_response(final_response)

        mock_client = AsyncMock()
        mock_client.post = mock_post

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm.LLM_MODEL", "test-model"), \
             patch("services.llm.execute_tool", return_value='[{"service": "nginx", "error_count": 23}]'), \
             patch("services.llm._prefetch_context", return_value=[]):
            result = await analyze(mock_client, pool, "What services have errors?")

        assert result.iterations == 2
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "get_service_overview"
        assert result.confidence == "medium"

    @pytest.mark.asyncio
    async def test_multi_tool_iterations(self):
        """LLM makes multiple tool calls across iterations."""
        from services.llm import analyze

        responses = [
            # Iteration 1: call get_service_overview
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "get_service_overview", "arguments": {}}}],
                },
                "done": True,
            },
            # Iteration 2: call search_templates
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_templates", "arguments": {"query": "error"}}}],
                },
                "done": True,
            },
            # Iteration 3: final answer
            {
                "message": {
                    "role": "assistant",
                    "content": "Found multiple error patterns. Confidence: high",
                },
                "done": True,
            },
        ]

        call_idx = 0

        async def mock_post(url, **kwargs):
            nonlocal call_idx
            resp = responses[call_idx]
            call_idx += 1
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.json = MagicMock(return_value=resp)
            return mock

        mock_client = AsyncMock()
        mock_client.post = mock_post

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm.LLM_MODEL", "test-model"), \
             patch("services.llm.execute_tool", return_value='[]'), \
             patch("services.llm._prefetch_context", return_value=[]):
            result = await analyze(mock_client, pool, "What's going on?")

        assert result.iterations == 3
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].tool_name == "get_service_overview"
        assert result.tool_calls[1].tool_name == "search_templates"

    @pytest.mark.asyncio
    async def test_max_iterations_cap(self):
        """Loop terminates at MAX_ITERATIONS and returns partial result."""
        from services.llm import analyze

        # Always return tool calls to force hitting the cap
        endless_response = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "get_service_overview", "arguments": {}}}],
            },
            "done": True,
        }

        async def mock_post(url, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.json = MagicMock(return_value=endless_response)
            return mock

        mock_client = AsyncMock()
        mock_client.post = mock_post

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm.LLM_MODEL", "test-model"), \
             patch("services.llm.LLM_MAX_ITERATIONS", 3), \
             patch("services.llm.execute_tool", return_value='[]'), \
             patch("services.llm._prefetch_context", return_value=[]):
            result = await analyze(mock_client, pool, "Loop forever")

        assert result.iterations == 3
        assert "incomplete" in result.explanation.lower()
        assert result.confidence == "low"

    @pytest.mark.asyncio
    async def test_ollama_timeout(self):
        """Timeout from Ollama raises LLMTimeoutError."""
        from services.llm import analyze
        from errors import LLMTimeoutError

        async def mock_post(url, **kwargs):
            raise httpx.TimeoutException("Connection timed out")

        mock_client = AsyncMock()
        mock_client.post = mock_post

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm._prefetch_context", return_value=[]), \
             pytest.raises(LLMTimeoutError):
            await analyze(mock_client, pool, "test query")

    @pytest.mark.asyncio
    async def test_ollama_http_error(self):
        """HTTP error from Ollama raises LLMError."""
        from services.llm import analyze
        from errors import LLMError

        mock_response = MagicMock()
        mock_response.status_code = 500

        async def mock_post(url, **kwargs):
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=MagicMock(),
                response=mock_response,
            )

        mock_client = AsyncMock()
        mock_client.post = mock_post

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm._prefetch_context", return_value=[]), \
             pytest.raises(LLMError):
            await analyze(mock_client, pool, "test query")

    @pytest.mark.asyncio
    async def test_malformed_tool_arguments(self):
        """String arguments from Ollama are parsed as JSON."""
        from services.llm import analyze

        responses = [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "query_logs",
                                "arguments": '{"host": "gpu-node"}',  # string instead of dict
                            }
                        }
                    ],
                },
                "done": True,
            },
            {
                "message": {
                    "role": "assistant",
                    "content": "Found logs on gpu-node.",
                },
                "done": True,
            },
        ]

        call_idx = 0

        async def mock_post(url, **kwargs):
            nonlocal call_idx
            resp = responses[call_idx]
            call_idx += 1
            mock = MagicMock()
            mock.status_code = 200
            mock.raise_for_status = MagicMock()
            mock.json = MagicMock(return_value=resp)
            return mock

        mock_client = AsyncMock()
        mock_client.post = mock_post

        pool = MockPool()

        with patch("services.llm.GATEWAY_URL", "http://test:8001"), \
             patch("services.llm.LLM_MODEL", "test-model"), \
             patch("services.llm.execute_tool", return_value='[]'), \
             patch("services.llm._prefetch_context", return_value=[]):
            result = await analyze(mock_client, pool, "What happened on gpu-node?")

        assert result.iterations == 2
        assert result.tool_calls[0].arguments == {"host": "gpu-node"}

    @pytest.mark.asyncio
    async def test_context_filters_injected(self):
        """Context filters from AnalyzeRequest are injected into user message."""
        from services.llm import _build_user_message

        msg = _build_user_message("What happened?", {
            "service": "nginx.service",
            "host": "gpu-node",
            "start_time": "2026-01-15T00:00:00",
        })

        assert "What happened?" in msg
        assert "service: nginx.service" in msg
        assert "host: gpu-node" in msg
        assert "start_time: 2026-01-15T00:00:00" in msg


# =============================================================================
# Endpoint Tests
# =============================================================================

class TestAnalyzeEndpoint:
    """Tests for the POST /analyze API endpoint."""

    @pytest.mark.asyncio
    async def test_analyze_valid_request(self):
        """Valid request returns 200 with AnalyzeResponse."""
        from services.llm import AnalyzeResult, ToolCallRecord as LLMToolCallRecord

        mock_result = AnalyzeResult(
            query="What services have errors?",
            explanation="nginx has 23 errors.",
            confidence="high",
            tool_calls=[
                LLMToolCallRecord(
                    tool_name="get_service_overview",
                    arguments={"hours": 24},
                    result_summary='[{"service": "nginx", "error_count": 23}]',
                )
            ],
            sources=[{"service": "nginx", "error_count": 23}],
            iterations=2,
            model="test-model",
        )

        mock_pool = MockPool()

        with patch("db.database._pool", mock_pool), \
             patch("db.database.get_pool", return_value=mock_pool), \
             patch("api.routes.llm_analyze", return_value=mock_result):
            from main import app
            app.state.http_client = AsyncMock()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/analyze", json={
                    "query": "What services have errors?",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "What services have errors?"
        assert data["explanation"] == "nginx has 23 errors."
        assert data["confidence"] == "high"
        assert data["iterations"] == 2
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["tool_name"] == "get_service_overview"

    @pytest.mark.asyncio
    async def test_analyze_missing_query(self):
        """Missing query field returns 422."""
        mock_pool = MockPool()

        with patch("db.database._pool", mock_pool), \
             patch("db.database.get_pool", return_value=mock_pool):
            from main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/analyze", json={})

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_analyze_with_context_filters(self):
        """Request with context filters passes them to analyze()."""
        from services.llm import AnalyzeResult

        mock_result = AnalyzeResult(
            query="What happened?",
            explanation="Analysis complete.",
            confidence="medium",
            iterations=1,
            model="test-model",
        )

        mock_pool = MockPool()
        captured_context = {}

        async def capture_analyze(http_client, pool, query, context=None):
            captured_context.update(context or {})
            return mock_result

        with patch("db.database._pool", mock_pool), \
             patch("db.database.get_pool", return_value=mock_pool), \
             patch("api.routes.llm_analyze", side_effect=capture_analyze):
            from main import app
            app.state.http_client = AsyncMock()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/analyze", json={
                    "query": "What happened?",
                    "host": "gpu-node",
                    "service": "docker.service",
                })

        assert resp.status_code == 200
        assert captured_context.get("host") == "gpu-node"
        assert captured_context.get("service") == "docker.service"

    @pytest.mark.asyncio
    async def test_analyze_llm_error(self):
        """Ollama failure returns 502."""
        from errors import LLMError

        mock_pool = MockPool()

        with patch("db.database._pool", mock_pool), \
             patch("db.database.get_pool", return_value=mock_pool), \
             patch("api.routes.llm_analyze", side_effect=LLMError("Ollama is down")):
            from main import app
            app.state.http_client = AsyncMock()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/analyze", json={
                    "query": "test query",
                })

        assert resp.status_code == 502
        data = resp.json()
        assert data["error_code"] == "LLM_ERROR"

    @pytest.mark.asyncio
    async def test_analyze_timeout_error(self):
        """Ollama timeout returns 504."""
        from errors import LLMTimeoutError

        mock_pool = MockPool()

        with patch("db.database._pool", mock_pool), \
             patch("db.database.get_pool", return_value=mock_pool), \
             patch("api.routes.llm_analyze", side_effect=LLMTimeoutError("Timed out")):
            from main import app
            app.state.http_client = AsyncMock()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/analyze", json={
                    "query": "test query",
                })

        assert resp.status_code == 504
        data = resp.json()
        assert data["error_code"] == "LLM_TIMEOUT"
