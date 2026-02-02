#!/usr/bin/env python3
"""
Migration 003: Create log_templates table and add template_id to log_events

Creates the log_templates table for canonicalized, deduplicated log templates
with embedding versioning. Adds template_id FK column to log_events.

Run:      python db/migrations/003_create_log_templates.py
Rollback: python db/migrations/003_create_log_templates.py --rollback
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db.database import get_connection


def migrate():
    """Create log_templates table and add template_id to log_events."""
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            # Check if log_templates table already exists
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name = 'log_templates'
            """)
            if cursor.fetchone()['cnt'] > 0:
                print("Table 'log_templates' already exists, skipping creation...")
            else:
                print("Creating 'log_templates' table...")
                cursor.execute("""
                    CREATE TABLE log_templates (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        template_hash VARCHAR(32) NOT NULL,
                        canonical_text TEXT NOT NULL,
                        service VARCHAR(255) NOT NULL,
                        level ENUM('DEBUG','INFO','WARN','WARNING','ERROR','CRITICAL','FATAL') NOT NULL,
                        embedding_vector VECTOR(4096) NOT NULL,
                        embedding_model VARCHAR(100) NOT NULL DEFAULT 'qwen3-embedding:8b',
                        embedding_dim INT NOT NULL DEFAULT 4096,
                        canon_version VARCHAR(10) NOT NULL DEFAULT 'v1',
                        canon_hash VARCHAR(32) NOT NULL,
                        chunk_version VARCHAR(10) NOT NULL DEFAULT 'v1',
                        first_seen DATETIME(6) NOT NULL,
                        last_seen DATETIME(6) NOT NULL,
                        event_count BIGINT NOT NULL DEFAULT 1,
                        source_hosts JSON DEFAULT NULL,
                        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                        UNIQUE INDEX idx_template_hash (template_hash),
                        INDEX idx_canon_version (canon_version),
                        INDEX idx_service (service),
                        INDEX idx_last_seen (last_seen)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                print("Table 'log_templates' created")

                # HNSW vector index — embedding_vector is NOT NULL from creation,
                # so index works immediately
                print("Creating HNSW vector index on log_templates...")
                cursor.execute("""
                    CREATE VECTOR INDEX idx_template_embedding
                    ON log_templates (embedding_vector)
                """)
                print("Vector index created")

            # Add template_id to log_events if not present
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'log_events'
                  AND column_name = 'template_id'
            """)
            if cursor.fetchone()['cnt'] > 0:
                print("Column 'template_id' already exists on log_events, skipping...")
            else:
                print("Adding 'template_id' column to log_events...")
                cursor.execute("""
                    ALTER TABLE log_events
                    ADD COLUMN template_id BIGINT DEFAULT NULL
                """)
                cursor.execute("""
                    CREATE INDEX idx_template_id ON log_events (template_id)
                """)
                print("Column 'template_id' added with index")

            conn.commit()
            print("Migration 003 complete")
            return True

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


def rollback():
    """Remove log_templates table and template_id column."""
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            print("Dropping template_id column from log_events...")
            cursor.execute("ALTER TABLE log_events DROP COLUMN IF EXISTS template_id")

            print("Dropping vector index...")
            cursor.execute("DROP INDEX IF EXISTS idx_template_embedding ON log_templates")

            print("Dropping log_templates table...")
            cursor.execute("DROP TABLE IF EXISTS log_templates")

            conn.commit()
            print("Rollback complete")
            return True

    except Exception as e:
        conn.rollback()
        print(f"Rollback failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create log_templates table for canonicalized log deduplication')
    parser.add_argument('--rollback', action='store_true', help='Rollback the migration')
    args = parser.parse_args()

    if args.rollback:
        rollback()
    else:
        migrate()
