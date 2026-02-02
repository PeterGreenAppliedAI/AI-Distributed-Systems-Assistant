"""
DevMesh Platform - Test Fixtures
"""

import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Set test environment variables before importing app modules
os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "devmesh_test")
os.environ.setdefault("API_AUTH_ENABLED", "false")
os.environ.setdefault("API_KEY", "test-api-key-12345")
os.environ.setdefault("NODE_NAME", "test-node")


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class MockCursor:
    """Mock async cursor for DB operations."""

    def __init__(self, results=None):
        self.results = results or []
        self.rowcount = 0
        self._executed = []

    async def execute(self, sql, params=None):
        self._executed.append((sql, params))
        if "SELECT 1" in sql:
            self.results = [{"1": 1}]
        elif "SELECT COUNT" in sql and "log_hash" in sql:
            self.results = [{"cnt": 1}]
        elif "SELECT COUNT" in sql and "embedding_vector" in sql:
            self.results = [{"cnt": 0}]
        elif "SELECT COUNT" in sql and "log_templates" in sql:
            # Template table does not exist in test env — use old ingest path
            self.results = [{"cnt": 0}]
        elif "SELECT COUNT" in sql and "template_id" in sql:
            # template_id column does not exist in test env
            self.results = [{"cnt": 0}]
        elif "SELECT" in sql and "FROM log_events" in sql:
            # Query endpoint - return empty list (no rows)
            self.results = []
        self.rowcount = len(self.results)

    async def executemany(self, sql, rows):
        self._executed.append((sql, rows))
        self.rowcount = len(rows)

    async def fetchone(self):
        return self.results[0] if self.results else None

    async def fetchall(self):
        return self.results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockConnection:
    """Mock async connection."""

    def __init__(self):
        pass

    def cursor(self, *args, **kwargs):
        # Fresh cursor each time so results don't leak between calls
        return MockCursor()

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockPool:
    """Mock async connection pool."""

    def __init__(self, conn=None):
        self._conn = conn or MockConnection()

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


@pytest.fixture
def mock_pool():
    """Provide a mock DB pool."""
    return MockPool()


@pytest.fixture
def mock_cursor():
    """Provide a mock cursor with configurable results."""
    return MockCursor()


@pytest_asyncio.fixture
async def async_client(mock_pool):
    """Create an async test client with a mocked DB pool."""
    # Patch the pool before importing the app
    with patch("db.database._pool", mock_pool), \
         patch("db.database.get_pool", return_value=mock_pool):
        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def sample_log_event():
    """Return a valid log event dict."""
    return {
        "timestamp": "2025-12-01T12:00:00Z",
        "source": "journald",
        "service": "test.service",
        "host": "test-node",
        "level": "INFO",
        "message": "Test log message",
    }


@pytest.fixture
def sample_log_batch(sample_log_event):
    """Return a batch of log events."""
    return {"logs": [sample_log_event]}
