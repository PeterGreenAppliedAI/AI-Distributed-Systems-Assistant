"""
DevMesh Platform - Database Module
Handles MariaDB connections: async pool for API, sync for CLI scripts.
"""

import os
import logging
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Allowlist of tables that can be described (B3 - SQL injection prevention)
_ALLOWED_TABLES = {"log_events", "log_templates"}

# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _get_required_env(key: str, default: str = None) -> str:
    """Get environment variable, raising error if required and missing."""
    value = os.getenv(key, default)
    if value is None:
        raise RuntimeError(f"Required environment variable {key} is not set")
    return value


def _db_config() -> dict:
    """Build DB config dict from environment."""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'devmesh'),
        'password': _get_required_env('DB_PASSWORD'),
        'db': os.getenv('DB_NAME', 'devmesh'),
    }

# ---------------------------------------------------------------------------
# Async connection pool (H1, H2) - used by FastAPI routes
# ---------------------------------------------------------------------------

_pool = None  # aiomysql.Pool | None


async def init_pool():
    """Create the async connection pool. Call once at app startup."""
    global _pool
    import aiomysql

    cfg = _db_config()
    _pool = await aiomysql.create_pool(
        host=cfg['host'],
        port=cfg['port'],
        user=cfg['user'],
        password=cfg['password'],
        db=cfg['db'],
        charset='utf8mb4',
        autocommit=False,
        minsize=int(os.getenv('DB_POOL_MIN_SIZE', 2)),
        maxsize=int(os.getenv('DB_POOL_MAX_SIZE', 10)),
        cursorclass=aiomysql.DictCursor,
    )
    logger.info("Async DB pool initialised (min=%d, max=%d)", _pool.minsize, _pool.maxsize)


async def close_pool():
    """Close the async pool. Call once at app shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("Async DB pool closed")


def get_pool():
    """Return the live pool (for use in routes)."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised - call init_pool() first")
    return _pool


# ---------------------------------------------------------------------------
# Sync helpers - used by CLI scripts (ttl_cleanup, migrations, setup)
# ---------------------------------------------------------------------------

def get_sync_connection():
    """Create and return a new synchronous pymysql connection."""
    cfg = _db_config()
    return pymysql.connect(
        host=cfg['host'],
        port=cfg['port'],
        user=cfg['user'],
        password=cfg['password'],
        database=cfg['db'],
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=False,
    )


# Keep old name as alias so existing callers keep working
get_connection = get_sync_connection


def create_log_events_table():
    """Create the log_events table if it doesn't exist."""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS log_events (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        timestamp DATETIME(6) NOT NULL,
        source VARCHAR(255) NOT NULL COMMENT 'Component or exporter name',
        service VARCHAR(255) NOT NULL COMMENT 'Logical service name',
        host VARCHAR(255) NOT NULL COMMENT 'Node or VM name',
        level ENUM('DEBUG', 'INFO', 'WARN', 'WARNING', 'ERROR', 'CRITICAL', 'FATAL') NOT NULL,
        trace_id VARCHAR(64) DEFAULT NULL COMMENT 'Distributed trace ID',
        span_id VARCHAR(32) DEFAULT NULL COMMENT 'Span ID within trace',
        event_type VARCHAR(100) DEFAULT NULL COMMENT 'e.g. http_request, db_error',
        error_code VARCHAR(50) DEFAULT NULL COMMENT 'e.g. ECONNRESET, HTTP_500',
        message TEXT NOT NULL COMMENT 'Log message content',
        meta_json JSON DEFAULT NULL COMMENT 'Extra metadata',

        INDEX idx_timestamp (timestamp),
        INDEX idx_service (service),
        INDEX idx_host (host),
        INDEX idx_level (level),
        INDEX idx_service_timestamp (service, timestamp),
        INDEX idx_host_timestamp (host, timestamp),
        INDEX idx_trace_id (trace_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='Core log events table for DevMesh Platform';
    """

    conn = get_sync_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
            conn.commit()
            print("log_events table created successfully")
        return True
    except Exception as e:
        print(f"Error creating log_events table: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def test_connection() -> bool:
    """Test database connection (sync)."""
    try:
        conn = get_sync_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()
            logger.info("Connected to MariaDB: %s", version['VERSION()'])
        conn.close()
        return True
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        return False


async def async_test_connection() -> bool:
    """Test database connection via the async pool."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        return True
    except Exception:
        return False


def get_table_info(table_name: str = 'log_events'):
    """Get table schema information. Table name is validated against allowlist (B3)."""
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not in the allowed tables list: {_ALLOWED_TABLES}")

    conn = get_sync_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DESCRIBE `log_events`" if table_name == "log_events" else f"DESCRIBE `{table_name}`")
            columns = cursor.fetchall()
            print(f"\n{table_name} schema:")
            print("-" * 80)
            for col in columns:
                print(f"{col['Field']:20} {col['Type']:30} {col['Null']:5} {col['Key']:5}")
        return columns
    except Exception as e:
        print(f"Error getting table info: {e}")
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    print("DevMesh Platform - Database Setup")
    print("=" * 80)

    if test_connection():
        create_log_events_table()
        get_table_info('log_events')
        print("\n" + "=" * 80)
        print("Database setup complete!")
    else:
        print("Database setup failed - check connection settings")
