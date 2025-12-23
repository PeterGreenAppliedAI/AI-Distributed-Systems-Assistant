#!/usr/bin/env python3
"""
DevMesh TTL Cleanup Job
Deletes log entries older than the configured retention period.

Usage:
    python ttl_cleanup.py                    # Uses default 90 days
    python ttl_cleanup.py --days 30          # Custom retention
    python ttl_cleanup.py --dry-run          # Preview without deleting
    python ttl_cleanup.py --batch-size 10000 # Custom batch size

Can be run via:
    - Manual execution
    - Cron job (recommended: daily at 3 AM)
    - Systemd timer
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import mysql.connector
from dotenv import load_dotenv

# Load environment from project root
load_dotenv(Path(__file__).parent.parent / '.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Create database connection from environment variables."""
    return mysql.connector.connect(
        host=os.getenv('DB_HOST', '10.0.0.18'),
        port=int(os.getenv('DB_PORT', 3306)),
        database=os.getenv('DB_NAME', 'devmesh'),
        user=os.getenv('DB_USER', 'devmesh'),
        password=os.getenv('DB_PASSWORD', ''),
        autocommit=False
    )


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
        'total_logs': total[0],
        'oldest_log': total[1],
        'newest_log': total[2],
        'to_delete': to_delete[0],
        'table_size_mb': size[0] if size else 0
    }


def delete_old_logs(retention_days=90, batch_size=5000, dry_run=False):
    """
    Delete logs older than retention_days.

    Uses batched deletes to avoid long-running transactions and table locks.
    """
    cutoff_date = datetime.now() - timedelta(days=retention_days)

    logger.info(f"TTL Cleanup Started")
    logger.info(f"  Retention: {retention_days} days")
    logger.info(f"  Cutoff date: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Batch size: {batch_size}")
    logger.info(f"  Dry run: {dry_run}")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get pre-cleanup stats
        stats_before = get_stats(cursor, cutoff_date)
        logger.info(f"")
        logger.info(f"Current state:")
        logger.info(f"  Total logs: {stats_before['total_logs']:,}")
        logger.info(f"  Oldest log: {stats_before['oldest_log']}")
        logger.info(f"  Newest log: {stats_before['newest_log']}")
        logger.info(f"  Table size: {stats_before['table_size_mb']} MB")
        logger.info(f"  Logs to delete: {stats_before['to_delete']:,}")

        if stats_before['to_delete'] == 0:
            logger.info(f"")
            logger.info(f"No logs older than {retention_days} days. Nothing to delete.")
            return {'deleted': 0, 'batches': 0}

        if dry_run:
            logger.info(f"")
            logger.info(f"DRY RUN - No logs will be deleted")
            logger.info(f"Would delete {stats_before['to_delete']:,} logs")
            return {'deleted': 0, 'batches': 0, 'would_delete': stats_before['to_delete']}

        # Perform batched deletes
        total_deleted = 0
        batch_num = 0

        logger.info(f"")
        logger.info(f"Starting deletion...")

        while True:
            batch_num += 1

            # Delete in batches to avoid long locks
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
            logger.info(f"  Batch {batch_num}: deleted {deleted_count:,} logs (total: {total_deleted:,})")

        # Get post-cleanup stats
        stats_after = get_stats(cursor, cutoff_date)

        logger.info(f"")
        logger.info(f"Cleanup complete:")
        logger.info(f"  Total deleted: {total_deleted:,} logs")
        logger.info(f"  Batches: {batch_num}")
        logger.info(f"  Remaining logs: {stats_after['total_logs']:,}")
        logger.info(f"  New oldest log: {stats_after['oldest_log']}")
        logger.info(f"  Table size: {stats_after['table_size_mb']} MB")

        # Calculate space reclaimed (approximate - actual reclaim needs OPTIMIZE TABLE)
        space_before = stats_before['table_size_mb'] or 0
        space_after = stats_after['table_size_mb'] or 0

        if total_deleted > 0:
            logger.info(f"")
            logger.info(f"Note: Run 'OPTIMIZE TABLE log_events' to reclaim disk space")

        return {
            'deleted': total_deleted,
            'batches': batch_num,
            'remaining': stats_after['total_logs']
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Error during cleanup: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='DevMesh TTL Cleanup - Delete old log entries'
    )
    parser.add_argument(
        '--days', '-d',
        type=int,
        default=90,
        help='Retention period in days (default: 90)'
    )
    parser.add_argument(
        '--batch-size', '-b',
        type=int,
        default=5000,
        help='Number of rows to delete per batch (default: 5000)'
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Preview what would be deleted without actually deleting'
    )

    args = parser.parse_args()

    try:
        result = delete_old_logs(
            retention_days=args.days,
            batch_size=args.batch_size,
            dry_run=args.dry_run
        )

        if args.dry_run:
            logger.info(f"Dry run complete. Would delete {result.get('would_delete', 0):,} logs.")
        else:
            logger.info(f"TTL cleanup finished. Deleted {result['deleted']:,} logs.")

    except Exception as e:
        logger.error(f"TTL cleanup failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
