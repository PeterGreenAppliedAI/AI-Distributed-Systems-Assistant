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
async def test_failed_schema_check_fails_closed_and_not_cached():
    from errors import DatabaseConnectionError
    _reset_schema_cache()
    with patch("api.routes.get_pool", side_effect=RuntimeError("pool not initialised")):
        with pytest.raises(DatabaseConnectionError):
            await api.routes._check_templates_table_exists()
    assert api.routes._has_templates_table is None  # retried next time
    _reset_schema_cache()


@pytest.mark.asyncio
async def test_ingest_returns_503_when_schema_check_fails():
    """Must not silently fall back to the duplicate-prone legacy ingest path."""
    class FlakySchemaCursor(FakeCursor):
        async def execute(self, sql, params=None):
            if "information_schema" in sql:
                raise RuntimeError("transient information_schema failure")
            await super().execute(sql, params)

    class FlakyConn(FakeConn):
        def cursor(self, *a, **kw):
            return FlakySchemaCursor(self.db)

    class FlakyPool(FakePool):
        def acquire(self):
            return FlakyConn(self.db)

    db = FakeDB()
    _reset_schema_cache()
    with patch("api.routes.get_pool", return_value=FlakyPool(db)):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/ingest/logs", json={"logs": [_log()]})
    assert r.status_code == 503, r.text
    assert not db.log_hashes  # nothing written via the degraded path
    _reset_schema_cache()


# =============================================================================
# HTTP search routes: placeholder order with filters
# =============================================================================


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


# =============================================================================
# HTTP search routes: VEC_FromText placeholder must receive the vector
# =============================================================================

class SchemaAwareRecordingCursor(RecordingCursor):
    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.results = [{"cnt": 1}] if "information_schema" in sql else []


def _search_pool():
    return RecordingPool(SchemaAwareRecordingCursor())


def _vector_sql(cur):
    return next((sql, params) for sql, params in cur.executed if "VEC_DISTANCE_COSINE" in sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,extra", [
    ("/search/templates", {"service": "docker.service", "level": "ERROR"}),
    ("/search/logs", {"service": "docker.service", "host": "gpu-node"}),
])
async def test_http_search_param_order_with_filters(path, extra):
    pool = _search_pool()
    _reset_schema_cache()
    with patch("api.routes.get_pool", return_value=pool), \
         patch("api.routes.embed_text", new=AsyncMock(return_value=[0.5, 0.5])):
        from main import app
        app.state.http_client = object()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(path, params={"query": "container restart", "limit": 5, **extra})
    assert r.status_code == 200, r.text
    sql, params = _vector_sql(pool.cur)
    assert params[0] == "[0.5,0.5]", params          # vector goes to VEC_FromText
    assert params[-1] == 5, params                   # limit goes to LIMIT
    for v in extra.values():
        assert v in params[1:-1]
    _reset_schema_cache()


# =============================================================================
# Ops scripts: drain + healthcheck
# =============================================================================

import subprocess
import sys
import threading
import http.server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stub_server(expect_key="k"):
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ok = self.headers.get("X-API-Key") == expect_key
            hits.append((ok, len(body["logs"])))
            self.send_response(201 if ok else 401)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


def _run_drain(spool, port, *extra):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "drain_failed_batches.py"),
         "--file", str(spool), "--api", f"http://127.0.0.1:{port}/", "--sleep", "0", *extra],
        capture_output=True, text=True, timeout=30, cwd=ROOT,
    )


def test_drain_full_run(tmp_path):
    srv, hits = _stub_server()
    try:
        spool = tmp_path / "failed_batches.jsonl"
        spool.write_text('{"logs":[{"m":1}]}\nNOT JSON\n{"logs":[]}\n{"logs":[{"m":2},{"m":3}]}\n')
        (tmp_path / "failed_batches.jsonl.drained").write_text("old\n")
        r = _run_drain(spool, srv.server_port, "--api-key", "k")
        assert r.returncode == 0, r.stderr
        assert hits == [(True, 1), (True, 2)]
        assert not spool.exists()
        assert (tmp_path / "failed_batches.jsonl.drained").read_text() == "old\n"  # not clobbered
        assert (tmp_path / "failed_batches.jsonl.rejected").read_text() == "NOT JSON\n"
        assert len(list(tmp_path.glob("failed_batches.jsonl.drained.*"))) == 1
    finally:
        srv.shutdown()


def test_drain_stops_on_401_and_resumes(tmp_path):
    srv, hits = _stub_server()
    try:
        spool = tmp_path / "failed_batches.jsonl"
        spool.write_text('{"logs":[{"m":1}]}\n{"logs":[{"m":2}]}\n')
        r = _run_drain(spool, srv.server_port, "--api-key", "wrong")
        assert r.returncode == 2 and "401" in r.stderr
        draining = tmp_path / "failed_batches.jsonl.draining"
        assert draining.exists()  # nothing lost
        r = _run_drain(spool, srv.server_port, "--api-key", "k")
        assert r.returncode == 0, r.stderr
        assert [h for h in hits if h[0]] == [(True, 1), (True, 1)]
    finally:
        srv.shutdown()


def test_drain_limit_then_resume(tmp_path):
    srv, hits = _stub_server()
    try:
        spool = tmp_path / "failed_batches.jsonl"
        spool.write_text("".join(f'{{"logs":[{{"m":{i}}}]}}\n' for i in range(3)))
        r = _run_drain(spool, srv.server_port, "--api-key", "k", "--limit", "2")
        assert r.returncode == 0 and len(hits) == 2
        assert (tmp_path / "failed_batches.jsonl.draining").exists()
        r = _run_drain(spool, srv.server_port, "--api-key", "k")
        assert r.returncode == 0 and len(hits) == 3   # no resends
        assert list(tmp_path.glob("*.drained"))
    finally:
        srv.shutdown()


def test_healthcheck_staleness_uses_db_side_age():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import healthcheck

    class Cur:
        def execute(self, sql):
            assert "UTC_TIMESTAMP" in sql
        def fetchone(self):
            return {"mx": datetime(2026, 11, 3, 3, 0), "age": 2000.0}

    class Conn:
        def cursor(self):
            return Cur()
        def close(self):
            pass

    with patch("db.database.get_sync_connection", return_value=Conn()):
        out = healthcheck.check_db_and_ingest()
    sev = {name: s for s, name, _ in out}
    assert sev["db:connect"] == "ok"
    assert sev["ingest:staleness"] == "crit"   # 2000s >= 1800s


def test_healthcheck_importable_without_syslog():
    """syslog is Unix-only; the checks must import on macOS/Windows too."""
    import importlib
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    saved = {k: sys.modules.pop(k) for k in ("healthcheck", "syslog") if k in sys.modules}
    sys.modules["syslog"] = None  # makes `import syslog` raise ImportError
    try:
        hc = importlib.import_module("healthcheck")
        assert callable(hc.check_db_and_ingest)
    finally:
        sys.modules.pop("syslog", None)
        sys.modules.pop("healthcheck", None)
        sys.modules.update(saved)


def test_recount_only_single_groupby_pass():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import reconsolidate_templates as rt

    executed, committed = [], []

    class Cur:
        rowcount = 3
        def execute(self, sql, params=None):
            executed.append(sql)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    class Conn:
        def cursor(self):
            return Cur()
        def commit(self):
            committed.append(True)
        def close(self):
            pass

    with patch.object(rt, "get_sync_connection", return_value=Conn()):
        rt.recount_only()

    assert executed == [rt.RECOUNT_SQL] and committed
    sql = rt.RECOUNT_SQL
    assert "GROUP BY template_id" in sql
    assert "LEFT JOIN" in sql and "COALESCE(x.c, 0)" in sql   # zero-event templates reset to 0
    assert "WHERE template_id = t.id" not in sql             # no correlated subquery
    # recount never touches ids/hashes, which keeps it safe with the API running
    assert "template_hash" not in sql and "SET t.id" not in sql


def test_recount_only_skips_live_api_guard():
    """--recount-only is cache-safe, so it must run even while the API is up."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import reconsolidate_templates as rt
    called = []
    with patch.object(rt, "_api_is_running", return_value=True), \
         patch.object(rt, "recount_only", side_effect=lambda: called.append(True)), \
         patch.object(rt, "reconsolidate", side_effect=AssertionError("full run")):
        assert rt.main(["--recount-only"]) == 0
    assert called


def test_full_reconsolidate_refuses_with_api_up():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import reconsolidate_templates as rt
    with patch.object(rt, "_api_is_running", return_value=True), \
         patch.object(rt, "reconsolidate", side_effect=AssertionError("must not run")):
        assert rt.main([]) == 2
