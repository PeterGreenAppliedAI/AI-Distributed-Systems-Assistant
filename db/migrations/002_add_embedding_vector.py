#!/usr/bin/env python3
"""
Migration 002: Add embedding_vector column for semantic search

Adds a nullable VECTOR(4096) column to log_events for storing
Qwen3-Embedding:8b embeddings.

MariaDB requires NOT NULL for VECTOR INDEX, so the HNSW index is created
separately via --create-index after backfill populates the column.

Run:   python db/migrations/002_add_embedding_vector.py
Index: python db/migrations/002_add_embedding_vector.py --create-index
Rollback: python db/migrations/002_add_embedding_vector.py --rollback
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db.database import get_connection


def migrate():
    """Add embedding_vector column (nullable, no index yet)."""
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            # Check if column already exists
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'log_events'
                  AND column_name = 'embedding_vector'
            """)
            result = cursor.fetchone()

            if result['cnt'] > 0:
                print("Column 'embedding_vector' already exists, skipping...")
                return True

            print("Adding 'embedding_vector' column (VECTOR(4096), nullable)...")
            cursor.execute("""
                ALTER TABLE log_events
                ADD COLUMN embedding_vector VECTOR(4096) DEFAULT NULL
            """)

            conn.commit()
            print("Migration complete: embedding_vector column added")
            print("Note: Run with --create-index after backfill to add HNSW index")
            return True

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


def create_index():
    """Create HNSW vector index (requires NOT NULL, so set column NOT NULL first).

    This should be run after backfill has populated all rows, or after setting
    NULL rows to a zero vector.
    """
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            # Check how many NULLs remain
            cursor.execute("SELECT COUNT(*) as cnt FROM log_events WHERE embedding_vector IS NULL")
            nulls = cursor.fetchone()['cnt']
            if nulls > 0:
                print(f"Warning: {nulls} rows still have NULL embedding_vector")
                print("The column will be set to NOT NULL — NULLs will block this.")
                print("Run backfill first, or pass --force to set NULLs to zero vector.")
                return False

            # Check if index already exists
            cursor.execute("SHOW INDEX FROM log_events WHERE Key_name = 'idx_embedding'")
            if cursor.fetchone():
                print("Index 'idx_embedding' already exists, skipping...")
                return True

            print("Setting embedding_vector to NOT NULL...")
            cursor.execute("""
                ALTER TABLE log_events
                MODIFY COLUMN embedding_vector VECTOR(4096) NOT NULL
            """)

            print("Creating HNSW vector index...")
            cursor.execute("""
                CREATE VECTOR INDEX idx_embedding ON log_events (embedding_vector)
            """)

            conn.commit()
            print("Vector index created successfully")
            return True

    except Exception as e:
        conn.rollback()
        print(f"Index creation failed: {e}")
        return False
    finally:
        conn.close()


def rollback():
    """Remove embedding_vector column and index."""
    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            print("Dropping vector index...")
            cursor.execute("DROP INDEX IF EXISTS idx_embedding ON log_events")

            print("Dropping column...")
            cursor.execute("ALTER TABLE log_events DROP COLUMN IF EXISTS embedding_vector")

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
    parser = argparse.ArgumentParser(description='Add embedding_vector column for semantic search')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--rollback', action='store_true', help='Rollback the migration')
    group.add_argument('--create-index', action='store_true',
                       help='Create HNSW vector index (run after backfill)')
    args = parser.parse_args()

    if args.rollback:
        rollback()
    elif args.create_index:
        create_index()
    else:
        migrate()
