"""Tests for API key authentication middleware (B1)."""

import os
import pytest
import pytest_asyncio
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def auth_client():
    """Create a test client with auth ENABLED."""
    os.environ["API_AUTH_ENABLED"] = "true"
    os.environ["API_KEY"] = "test-secret-key"
    os.environ["DB_PASSWORD"] = "test_password"

    from tests.conftest import MockPool
    mock_pool = MockPool()

    with patch("db.database._pool", mock_pool), \
         patch("db.database.get_pool", return_value=mock_pool):
        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    # Reset
    os.environ["API_AUTH_ENABLED"] = "false"


@pytest.mark.asyncio
async def test_public_paths_pass_without_key(auth_client):
    """Public paths should not require authentication."""
    for path in ["/health", "/info", "/", "/docs", "/openapi.json"]:
        resp = await auth_client.get(path)
        assert resp.status_code != 401, f"{path} should be public"


@pytest.mark.asyncio
async def test_protected_path_rejected_without_key(auth_client):
    """Protected paths should return 401 without API key."""
    resp = await auth_client.get("/query/logs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_path_rejected_with_wrong_key(auth_client):
    """Protected paths should return 401 with wrong API key."""
    resp = await auth_client.get(
        "/query/logs",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_path_accepted_with_correct_key(auth_client):
    """Protected paths should pass with correct API key."""
    resp = await auth_client.get(
        "/query/logs",
        headers={"X-API-Key": "test-secret-key"},
    )
    # Should not be 401 (may be other error due to mock DB, but auth passed)
    assert resp.status_code != 401
