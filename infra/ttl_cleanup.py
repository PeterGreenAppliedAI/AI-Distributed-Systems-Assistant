#!/usr/bin/env python3
"""
DevMesh TTL Cleanup Job
Deletes log entries older than the configured retention period.

Usage:
    python ttl_cleanup.py                    # Uses default 90 days
    python ttl_cleanup.py --days 30          # Custom retention
    python ttl_cleanup.py --dry-run          # Preview without deleting
    python ttl_cleanup.py --batch-size 10000 # Custom batch size
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Load environment from project root
load_dotenv(Path(__file__).parent.parent / '.env')

# Unified DB driver (M3) - use pymysql via shared helper
from db.database import get_sync_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Create database connection using shared helper (M3)."""
    # B4 - DB_PASSWORD is required by _get_required_env inside get_sync_connection
    return get_sync_connection()


def get_stats(cursor, cutoff_date):
    """Get statistics about logs to be deleted."""
    cursor.execute("""
        SELECT
            COUNT(*) as total_logs,
            MIN(timestamp) as oldest_log,
            MAX(timestamp) as newest_log
        FROM log_events
    """)
    total = cursor.fetchone()

    cursor.execute("""
        SELECT COUNT(*) as to_delete
        FROM log_events
        WHERE timestamp < %s
    """, (cutoff_date,))
    to_delete = cursor.fetchone()

    cursor.execute("""
        SELECT
            ROUND((data_length + index_length) / 1024 / 1024, 2) as size_mb
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = 'log_events'
    """, (os.getenv('DB_NAME', 'devmesh'),))
    size = cursor.fetchone()

    return {
        'total_logs': total['total_logs'],
        'oldest_log': total['oldest_log'],
        'newest_log': total['newest_log'],
        'to_delete': to_delete['to_delete'],
        'table_size_mb': size['size_mb'] if size else 0,
    }


def delete_stale_templates(cursor, conn, cutoff_date, dry_run=False):
    """Delete templates not seen since cutoff_date.

    Templates are considered stale if their last_seen timestamp is older
    than the cutoff. This keeps template growth bounded as log_events expire.
    """
    # Check if log_templates table exists
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM information_schema.tables
        WHERE table_schema = %s AND table_name = 'log_templates'
    """, (os.getenv('DB_NAME', 'devmesh'),))
    if cursor.fetchone()['cnt'] == 0:
        logger.info("Template cleanup: log_templates table not found, skipping")
        return {'deleted': 0}

    # Count stale templates
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM log_templates
        WHERE last_seen < %s
    """, (cutoff_date,))
    stale_count = cursor.fetchone()['cnt']

    if stale_count == 0:
        logger.info("Template cleanup: no stale templates found")
        return {'deleted': 0}

    logger.info("Template cleanup: %s stale templates (last_seen < %s)",
                f"{stale_count:,}", cutoff_date.strftime('%Y-%m-%d'))

    if dry_run:
        logger.info("Template cleanup: DRY RUN - would delete %s templates",
                    f"{stale_count:,}")
        return {'deleted': 0, 'would_delete': stale_count}

    # Delete stale templates (no batching needed, typically small count)
    cursor.execute("""
        DELETE FROM log_templates
        WHERE last_seen < %s
    """, (cutoff_date,))
    deleted = cursor.rowcount
    conn.commit()

    logger.info("Template cleanup: deleted %s stale templates", f"{deleted:,}")
    return {'deleted': deleted}


def delete_old_logs(retention_days=90, batch_size=5000, dry_run=False):
    """Delete logs older than retention_days using batched deletes."""
    cutoff_date = datetime.now() - timedelta(days=retention_days)

    logger.info("TTL Cleanup Started")
    logger.info("  Retention: %d days", retention_days)
    logger.info("  Cutoff date: %s", cutoff_date.strftime('%Y-%m-%d %H:%M:%S'))
    logger.info("  Batch size: %d", batch_size)
    logger.info("  Dry run: %s", dry_run)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        stats_before = get_stats(cursor, cutoff_date)
        logger.info("Current state:")
        logger.info("  Total logs: %s", f"{stats_before['total_logs']:,}")
        logger.info("  Oldest log: %s", stats_before['oldest_log'])
        logger.info("  Newest log: %s", stats_before['newest_log'])
        logger.info("  Table size: %s MB", stats_before['table_size_mb'])
        logger.info("  Logs to delete: %s", f"{stats_before['to_delete']:,}")

        if stats_before['to_delete'] == 0:
            logger.info("No logs older than %d days. Nothing to delete.", retention_days)
            # Still clean up stale templates
            template_result = delete_stale_templates(cursor, conn, cutoff_date, dry_run)
            return {'deleted': 0, 'batches': 0, 'templates_deleted': template_result.get('deleted', 0)}

        if dry_run:
            logger.info("DRY RUN - No logs will be deleted")
            logger.info("Would delete %s logs", f"{stats_before['to_delete']:,}")
            # Still check templates in dry run mode
            template_result = delete_stale_templates(cursor, conn, cutoff_date, dry_run)
            return {
                'deleted': 0,
                'batches': 0,
                'would_delete': stats_before['to_delete'],
                'templates_would_delete': template_result.get('would_delete', 0),
            }

        total_deleted = 0
        batch_num = 0
        logger.info("Starting deletion...")

        while True:
            batch_num += 1
            cursor.execute("""
                DELETE FROM log_events
                WHERE timestamp < %s
                LIMIT %s
            """, (cutoff_date, batch_size))

            deleted_count = cursor.rowcount
            conn.commit()

            if deleted_count == 0:
                break

            total_deleted += deleted_count
            logger.info("  Batch %d: deleted %s logs (total: %s)",
                        batch_num, f"{deleted_count:,}", f"{total_deleted:,}")

        stats_after = get_stats(cursor, cutoff_date)
        logger.info("Cleanup complete:")
        logger.info("  Total deleted: %s logs", f"{total_deleted:,}")
        logger.info("  Batches: %d", batch_num)
        logger.info("  Remaining logs: %s", f"{stats_after['total_logs']:,}")
        logger.info("  New oldest log: %s", stats_after['oldest_log'])
        logger.info("  Table size: %s MB", stats_after['table_size_mb'])

        if total_deleted > 0:
            logger.info("Note: Run 'OPTIMIZE TABLE log_events' to reclaim disk space")

        # Clean up stale templates (same retention period)
        template_result = delete_stale_templates(cursor, conn, cutoff_date, dry_run)

        return {
            'deleted': total_deleted,
            'batches': batch_num,
            'remaining': stats_after['total_logs'],
            'templates_deleted': template_result.get('deleted', 0),
        }

    except Exception as e:
        conn.rollback()
        logger.error("Error during cleanup: %s", e)
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='DevMesh TTL Cleanup - Delete old log entries',
    )
    parser.add_argument('--days', '-d', type=int, default=90,
                        help='Retention period in days (default: 90)')
    parser.add_argument('--batch-size', '-b', type=int, default=5000,
                        help='Number of rows to delete per batch (default: 5000)')
    parser.add_argument('--dry-run', '-n', action='store_true',
                        help='Preview what would be deleted without actually deleting')

    args = parser.parse_args()

    try:
        result = delete_old_logs(
            retention_days=args.days,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )

        if args.dry_run:
            logger.info("Dry run complete. Would delete %s logs.",
                        f"{result.get('would_delete', 0):,}")
        else:
            logger.info("TTL cleanup finished. Deleted %s logs, %s templates.",
                        f"{result['deleted']:,}",
                        f"{result.get('templates_deleted', 0):,}")

    except Exception as e:
        logger.error("TTL cleanup failed: %s", e)
        sys.exit(1)


if __name__ == '__main__':
    main()
