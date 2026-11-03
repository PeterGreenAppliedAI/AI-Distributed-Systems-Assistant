"""
Regression tests for issues raised in the Nov 2026 external review:
- template event_count inflation (create double-count, duplicate resends)
- failed schema checks must not be cached
- LLM tool argument clamping, valid-JSON truncation, search param order
- service-overview rows are not reported as cited sources
- UI + static assets reachable with auth enabled; data endpoints still protected
"""

import json
import os
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("API_AUTH_ENABLED", "false")

import api.routes
from services.template_cache import TemplateCache


# =============================================================================
# Stateful fake DB for the template-mode ingest path
# =============================================================================

class FakeDB:
    def __init__(self):
        self.templates: dict[str, dict] = {}   # template_hash -> row
        self.log_hashes: set[str] = set()
        self.next_tid = 1


class FakeCursor:
    def __init__(self, db: FakeDB):
        self.db = db
        self._results = []
        self.rowcount = 0
        self.lastrowid = None

    async def execute(self, sql, params=None):
        db = self.db
        self._results = []
        if "information_schema" in sql:
            if "log_hash" in sql:
                self._results = [{"cnt": 1}]
            elif "embedding_vector" in sql:
                self._results = [{"cnt": 1}]  # column exists, but template mode skips it
            elif "log_templates" in sql:
                self._results = [{"cnt": 1}]
            elif "template_id" in sql:
                self._results = [{"cnt": 1}]
        elif sql.strip().startswith("SELECT id, template_hash FROM log_templates"):
            self._results = [{"id": db.templates[h]["id"], "template_hash": h}
                             for h in params if h in db.templates]
        elif "INSERT INTO log_templates" in sql:
            m = re.search(r"%s, %s, (\d+), %s\)", sql)
            initial = int(m.group(1))
            t_hash = params[0]
            db.templates[t_hash] = {"id": db.next_tid, "event_count": initial}
            self.lastrowid = db.next_tid
            db.next_tid += 1
        elif sql.strip().startswith("SELECT log_hash FROM log_events"):
            self._results = [{"log_hash": h} for h in params if h in db.log_hashes]
        elif "UPDATE log_templates" in sql and "event_count = event_count +" in sql:
            cnt, _ts, t_hash = params
            if t_hash in db.templates:
                db.templates[t_hash]["event_count"] += cnt
        self.rowcount = len(self._results)

    async def executemany(self, sql, rows):
        inserted = 0
        for row in rows:
            h = row[0]  # log_hash is first column when present
            if h not in self.db.log_hashes:
                self.db.log_hashes.add(h)
                inserted += 1
        self.rowcount = inserted

    async def fetchone(self):
        return self._results[0] if self._results else None

    async def fetchall(self):
        return self._results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self, *a, **kw):
        return FakeCursor(self.db)

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class FakePool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        return FakeConn(self.db)


def _reset_schema_cache():
    api.routes._has_hash_column = None
    api.routes._has_embedding_column = None
    api.routes._has_templates_table = None
    api.routes._has_template_id_column = None


@pytest_asyncio.fixture
async def template_client():
    db = FakeDB()
    pool = FakePool(db)
    _reset_schema_cache()
    with patch("api.routes.get_pool", return_value=pool), \
         patch("db.database._pool", pool), \
         patch("api.routes.embed_batch", new=AsyncMock(side_effect=lambda c, texts: [[0.1] * 4 for _ in texts])):
        from main import app
        app.state.template_cache = TemplateCache()
        app.state.http_client = object()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db
    _reset_schema_cache()


def _log(msg="Connection from 10.0.0.5 accepted", ts="2026-11-03T03:00:00Z"):
    return {"timestamp": ts, "source": "journald", "service": "sshd",
            "host": "gpu-node", "level": "INFO", "message": msg}


@pytest.mark.asyncio
async def test_new_template_counted_once(template_client):
    client, db = template_client
    r = await client.post("/ingest/logs", json={"logs": [_log()]})
    assert r.status_code == 201, r.text
    assert r.json()["ingested"] == 1
    assert [t["event_count"] for t in db.templates.values()] == [1]


@pytest.mark.asyncio
async def test_duplicate_resend_does_not_inflate(template_client):
    client, db = template_client
    await client.post("/ingest/logs", json={"logs": [_log()]})
    r = await client.post("/ingest/logs", json={"logs": [_log()]})
    assert r.json()["duplicates"] == 1
    assert [t["event_count"] for t in db.templates.values()] == [1]


@pytest.mark.asyncio
async def test_same_template_different_events_counted(template_client):
    client, db = template_client
    logs = [
        _log("Connection from 10.0.0.5 accepted", "2026-11-03T03:00:00Z"),
        _log("Connection from 10.0.0.6 accepted", "2026-11-03T03:00:01Z"),
        _log("Connection from 10.0.0.6 accepted", "2026-11-03T03:00:01Z"),  # in-batch dup
    ]
    r = await client.post("/ingest/logs", json={"logs": logs})
    assert r.json()["ingested"] == 2
    assert len(db.templates) == 1
    assert [t["event_count"] for t in db.templates.values()] == [2]


# =============================================================================
# Schema check caching
# =============================================================================

@pytest.mark.asyncio
async def test_failed_schema_check_not_cached():
    _reset_schema_cache()
    with patch("api.routes.get_pool", side_effect=RuntimeError("pool not initialised")):
        assert await api.routes._check_templates_table_exists() is False
    assert api.routes._has_templates_table is None  # retried next time
    _reset_schema_cache()


# =============================================================================
# LLM tool layer
# =============================================================================

class RecordingCursor:
    def __init__(self, results=None):
        self.executed = []
        self.results = results or []

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchall(self):
        return self.results

    async def fetchone(self):
        return self.results[0] if self.results else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class RecordingPool:
    def __init__(self, cursor):
        self.cur = cursor

    def acquire(self):
        pool = self

        class _C:
            def cursor(self, *a, **k):
                return pool.cur

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass
        return _C()


@pytest.mark.asyncio
async def test_query_logs_limit_clamped():
    from services.llm_tools import execute_query_logs, MAX_LIMIT
    cur = RecordingCursor()
    await execute_query_logs(RecordingPool(cur), {"limit": 10_000_000})
    assert cur.executed[0][1][-1] == MAX_LIMIT
    await execute_query_logs(RecordingPool(cur), {"limit": "garbage"})
    assert cur.executed[1][1][-1] == 20


@pytest.mark.asyncio
async def test_template_stats_hours_clamped():
    from services.llm_tools import execute_get_template_stats, MAX_HOURS
    cur = RecordingCursor()
    await execute_get_template_stats(RecordingPool(cur), {"hours": 10**9, "limit": -5})
    params = cur.executed[0][1]
    cutoff = params[0]
    age_h = (datetime.utcnow() - cutoff).total_seconds() / 3600
    assert MAX_HOURS - 1 < age_h < MAX_HOURS + 1
    assert params[-1] == 1


@pytest.mark.asyncio
async def test_search_templates_param_order_with_filters():
    from services.llm_tools import execute_search_templates
    cur = RecordingCursor()
    with patch("services.llm_tools.embed_text", new=AsyncMock(return_value=[0.5, 0.25])):
        await execute_search_templates(
            RecordingPool(cur), object(),
            {"query": "ufw", "service": "kernel", "level": "WARN", "limit": 3},
        )
    sql, params = cur.executed[0]
    # Placeholder order in SQL: VEC_FromText, service, level, LIMIT
    assert params == ["[0.5,0.25]", "kernel", "WARN", 3]
    assert sql.index("VEC_FromText(%s)") < sql.index("service = %s")


def test_truncate_result_is_valid_json():
    from services.llm_tools import truncate_result
    data = [{"id": i, "message": "x" * 100} for i in range(50)]
    out = truncate_result(data, max_chars=1000)
    parsed = json.loads(out)
    assert len(out) <= 1000
    assert parsed[-1]["_truncated"] is True
    assert parsed[-1]["total"] == 50
    assert all("id" in item for item in parsed[:-1])
    assert len(parsed) > 1


def test_extract_sources_skips_overview_and_markers():
    from services.llm import _extract_sources, ToolCallRecord
    recs = [
        ToolCallRecord("get_service_overview", {}, json.dumps([{"service": "sshd", "total_events": 9}])),
        ToolCallRecord("query_logs", {}, json.dumps([{"id": 1, "message": "a"},
                                                     {"_truncated": True, "total": 99}])),
    ]
    assert _extract_sources(recs) == [{"id": 1, "message": "a"}]


# =============================================================================
# Auth + UI
# =============================================================================

@pytest_asyncio.fixture
async def auth_client():
    os.environ["API_AUTH_ENABLED"] = "true"
    os.environ["API_KEY"] = "test-secret-key"
    from tests.conftest import MockPool
    mock_pool = MockPool()
    with patch("db.database._pool", mock_pool), \
         patch("db.database.get_pool", return_value=mock_pool):
        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    os.environ["API_AUTH_ENABLED"] = "false"


@pytest.mark.asyncio
async def test_ui_reachable_with_auth_enabled(auth_client):
    r = await auth_client.get("/ui")
    assert r.status_code in (200, 307)
    for path in ["/static/index.html", "/static/vendor/marked.min.js", "/static/vendor/purify.min.js"]:
        r = await auth_client.get(path)
        assert r.status_code == 200, path


@pytest.mark.asyncio
async def test_analyze_still_protected_with_auth_enabled(auth_client):
    r = await auth_client.post("/analyze", json={"query": "x"})
    assert r.status_code == 401


def test_ui_has_no_external_scripts_and_sanitizes():
    html = open(os.path.join(os.path.dirname(__file__), "..", "static", "index.html")).read()
    assert "cdn.jsdelivr.net" not in html
    assert not re.search(r'<script[^>]+src="https?://', html)
    assert "DOMPurify.sanitize(marked.parse(" in html
    assert "innerHTML = marked.parse" not in html
