"""Tests for API routes (ingestion and query)."""

import pytest
import pytest_asyncio
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

from tests.conftest import MockPool, MockConnection, MockCursor


@pytest_asyncio.fixture
async def client():
    """Create a test client with mocked DB."""
    import os
    os.environ["API_AUTH_ENABLED"] = "false"
    os.environ["DB_PASSWORD"] = "test_password"

    mock_pool = MockPool()

    with patch("db.database._pool", mock_pool), \
         patch("db.database.get_pool", return_value=mock_pool):
        # Reset cached schema checks
        import api.routes
        api.routes._has_hash_column = None
        api.routes._has_embedding_column = None
        api.routes._has_templates_table = None
        api.routes._has_template_id_column = None

        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_ingest_happy_path(client, sample_log_batch):
    """Ingestion should return 201 with ingested count."""
    resp = await client.post("/ingest/logs", json=sample_log_batch)
    assert resp.status_code == 201
    data = resp.json()
    assert data["ingested"] >= 0
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_ingest_empty_batch(client):
    """Empty logs list should return 400."""
    resp = await client.post("/ingest/logs", json={"logs": []})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "EMPTY_BATCH"


@pytest.mark.asyncio
async def test_ingest_invalid_log(client):
    """Invalid log event should return 422 validation error."""
    resp = await client.post("/ingest/logs", json={"logs": [{"bad": "data"}]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_query_returns_list(client):
    """Query endpoint should return a list."""
    resp = await client.get("/query/logs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_query_with_filters(client):
    """Query with filter params should not error."""
    resp = await client.get("/query/logs", params={
        "service": "test.service",
        "host": "node-1",
        "level": "INFO",
        "limit": 10,
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_query_pagination(client):
    """Query with offset should not error."""
    resp = await client.get("/query/logs", params={"limit": 5, "offset": 10})
    assert resp.status_code == 200
