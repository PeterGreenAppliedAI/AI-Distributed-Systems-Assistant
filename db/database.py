"""
DevMesh Platform - Database Module
Handles MariaDB connection and schema creation
"""

import os
from typing import Optional
import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
def _get_required_env(key: str, default: str = None) -> str:
    """Get environment variable, raising error if required and missing."""
    value = os.getenv(key, default)
    if value is None:
        raise RuntimeError(f"Required environment variable {key} is not set")
    return value


DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'devmesh'),
    'password': _get_required_env('DB_PASSWORD'),  # No default - must be set
    'database': os.getenv('DB_NAME', 'devmesh'),
    'charset': 'utf8mb4',
    'cursorclass': DictCursor,
    'autocommit': False
}


def get_connection():
    """
    Create and return a new database connection.
    """
    return pymysql.connect(**DB_CONFIG)


def create_log_events_table():
    """
    Create the log_events table if it doesn't exist.
    Schema based on PRD Section 7.1
    """
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

        -- Indexes for common queries
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

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
            conn.commit()
            print("✓ log_events table created successfully")
        return True
    except Exception as e:
        print(f"✗ Error creating log_events table: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def test_connection() -> bool:
    """
    Test database connection.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()
            print(f"✓ Connected to MariaDB: {version['VERSION()']}")
        conn.close()
        return True
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        return False


def get_table_info(table_name: str = 'log_events'):
    """
    Get table schema information.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"DESCRIBE {table_name}")
            columns = cursor.fetchall()
            print(f"\n{table_name} schema:")
            print("-" * 80)
            for col in columns:
                print(f"{col['Field']:20} {col['Type']:30} {col['Null']:5} {col['Key']:5}")
        return columns
    except Exception as e:
        print(f"✗ Error getting table info: {e}")
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    """
    Standalone script to initialize database schema.
    Run: python db/database.py
    """
    print("DevMesh Platform - Database Setup")
    print("=" * 80)

    # Test connection
    if test_connection():
        # Create table
        create_log_events_table()

        # Show table schema
        get_table_info('log_events')

        print("\n" + "=" * 80)
        print("Database setup complete!")
    else:
        print("Database setup failed - check connection settings")
