#!/usr/bin/env python3
"""
Migration 001: Add log_hash column for deduplication

This adds a hash column to enable idempotent log ingestion.
Duplicate logs (same timestamp + host + service + message) will be skipped.

Run: python db/migrations/001_add_log_hash.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db.database import get_connection


def migrate():
    """Add log_hash column and unique index."""
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            # Check if column already exists
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'log_events'
                  AND column_name = 'log_hash'
            """)
            result = cursor.fetchone()

            if result['cnt'] > 0:
                print("Column 'log_hash' already exists, skipping...")
                return True

            print("Adding 'log_hash' column...")
            cursor.execute("""
                ALTER TABLE log_events
                ADD COLUMN log_hash VARCHAR(16) DEFAULT NULL
                COMMENT 'Hash for deduplication (SHA256 truncated)'
                AFTER id
            """)

            print("Creating unique index on log_hash...")
            cursor.execute("""
                CREATE UNIQUE INDEX idx_log_hash ON log_events (log_hash)
            """)

            conn.commit()
            print("✓ Migration complete: log_hash column and index added")
            return True

    except Exception as e:
        conn.rollback()
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


def rollback():
    """Remove log_hash column and index."""
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            print("Dropping index...")
            cursor.execute("DROP INDEX IF EXISTS idx_log_hash ON log_events")

            print("Dropping column...")
            cursor.execute("ALTER TABLE log_events DROP COLUMN IF EXISTS log_hash")

            conn.commit()
            print("✓ Rollback complete")
            return True

    except Exception as e:
        conn.rollback()
        print(f"✗ Rollback failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Add log_hash column for deduplication')
    parser.add_argument('--rollback', action='store_true', help='Rollback the migration')
    args = parser.parse_args()

    if args.rollback:
        rollback()
    else:
        migrate()
