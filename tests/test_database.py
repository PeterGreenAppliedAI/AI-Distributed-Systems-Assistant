"""Tests for database module (B3 allowlist, env requirements, pool)."""

import os
import pytest
from unittest.mock import patch


class TestTableAllowlist:
    """B3 - SQL injection prevention via table name allowlist."""

    def test_allowed_table(self):
        from db.database import _ALLOWED_TABLES
        assert "log_events" in _ALLOWED_TABLES

    def test_get_table_info_rejects_invalid_name(self):
        from db.database import get_table_info
        with pytest.raises(ValueError, match="not in the allowed tables"):
            get_table_info("users; DROP TABLE log_events;--")

    def test_get_table_info_rejects_unknown_table(self):
        from db.database import get_table_info
        with pytest.raises(ValueError):
            get_table_info("nonexistent_table")


class TestRequiredEnvVars:
    """B4 - DB_PASSWORD must be set, no empty default."""

    def test_missing_password_raises(self):
        from db.database import _get_required_env
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="DB_PASSWORD"):
                _get_required_env("DB_PASSWORD")

    def test_password_with_value_succeeds(self):
        from db.database import _get_required_env
        with patch.dict(os.environ, {"DB_PASSWORD": "secret"}):
            assert _get_required_env("DB_PASSWORD") == "secret"


class TestPoolLifecycle:
    """H1/H2 - async pool init/close."""

    def test_get_pool_before_init_raises(self):
        from db.database import get_pool
        import db.database as db_mod
        original = db_mod._pool
        db_mod._pool = None
        try:
            with pytest.raises(RuntimeError, match="not initialised"):
                get_pool()
        finally:
            db_mod._pool = original

    @pytest.mark.asyncio
    async def test_init_and_close_pool(self):
        """Verify init_pool and close_pool work (with mock)."""
        import db.database as db_mod
        from unittest.mock import AsyncMock

        mock_pool = type("Pool", (), {
            "minsize": 2, "maxsize": 10,
            "close": lambda self: None,
            "wait_closed": AsyncMock(),
        })()

        with patch("aiomysql.create_pool", new_callable=AsyncMock, return_value=mock_pool):
            await db_mod.init_pool()
            assert db_mod._pool is mock_pool

            await db_mod.close_pool()
            assert db_mod._pool is None
