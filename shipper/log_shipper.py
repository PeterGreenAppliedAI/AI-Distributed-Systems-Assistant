#!/usr/bin/env python3
"""
DevMesh Platform - Log Shipper
Ingests logs from journald and ships them to the DevMesh API
"""

import os
import sys
import json
import subprocess
from datetime import datetime, timedelta
from typing import List, Dict, Any
import requests
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

# Configuration
API_HOST = os.getenv('API_HOST', '127.0.0.1')
API_PORT = os.getenv('API_PORT', '8000')
API_BASE_URL = f"http://{API_HOST}:{API_PORT}"
BATCH_SIZE = int(os.getenv('SHIPPER_BATCH_SIZE', 500))
LOOKBACK_HOURS = int(os.getenv('SHIPPER_LOOKBACK_HOURS', 24))
NODE_NAME = os.getenv('NODE_NAME', 'dev-services')
STATE_FILE = os.getenv('SHIPPER_STATE_FILE', 'shipper/last_ingested.txt')


def get_journald_logs(since_hours: int = 24) -> List[Dict[str, Any]]:
    """
    Fetch logs from journald using journalctl.

    Args:
        since_hours: How many hours back to fetch logs

    Returns:
        List of log entries as dictionaries
    """
    print(f"Fetching journald logs from last {since_hours} hours...")

    # Build journalctl command
    # --output=json: Output in JSON format (one JSON object per line)
    # --since: Time window
    # --no-pager: Don't paginate output
    cmd = [
        'journalctl',
        '--output=json',
        f'--since={since_hours} hours ago',
        '--no-pager'
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        # Parse each line as a separate JSON object
        logs = []
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Failed to parse JSON line: {e}")
                    continue

        print(f"✓ Fetched {len(logs)} log entries from journald")
        return logs

    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to fetch journald logs: {e}")
        print(f"stderr: {e.stderr}")
        return []
    except Exception as e:
        print(f"✗ Error fetching journald logs: {e}")
        return []


def map_priority_to_level(priority: str) -> str:
    """
    Map journald priority to DevMesh log level.

    Journald priorities (syslog):
    0 - emerg, 1 - alert, 2 - crit, 3 - err, 4 - warning, 5 - notice, 6 - info, 7 - debug
    """
    priority_map = {
        '0': 'FATAL',      # emerg
        '1': 'CRITICAL',   # alert
        '2': 'CRITICAL',   # crit
        '3': 'ERROR',      # err
        '4': 'WARN',       # warning
        '5': 'INFO',       # notice
        '6': 'INFO',       # info
        '7': 'DEBUG'       # debug
    }
    return priority_map.get(str(priority), 'INFO')


def transform_journald_to_log_event(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform a journald log entry to DevMesh log_events schema.

    Args:
        entry: Raw journald entry

    Returns:
        Log event in DevMesh schema
    """
    # Extract timestamp (microseconds since epoch)
    timestamp_us = int(entry.get('__REALTIME_TIMESTAMP', '0'))
    timestamp = datetime.utcfromtimestamp(timestamp_us / 1_000_000)

    # Get service/unit name
    service = entry.get('_SYSTEMD_UNIT', entry.get('SYSLOG_IDENTIFIER', 'unknown'))

    # Get message
    message = entry.get('MESSAGE', '')

    # Get priority and map to level
    priority = entry.get('PRIORITY', '6')
    level = map_priority_to_level(priority)

    # Build log event
    log_event = {
        'timestamp': timestamp.isoformat() + 'Z',
        'source': 'journald',
        'service': service,
        'host': NODE_NAME,
        'level': level,
        'message': message,
    }

    # Add optional metadata
    meta_json = {}

    # Add useful journald metadata
    if '_PID' in entry:
        meta_json['pid'] = entry['_PID']
    if '_COMM' in entry:
        meta_json['comm'] = entry['_COMM']
    if 'SYSLOG_FACILITY' in entry:
        meta_json['facility'] = entry['SYSLOG_FACILITY']
    if '_HOSTNAME' in entry:
        meta_json['hostname'] = entry['_HOSTNAME']

    if meta_json:
        log_event['meta_json'] = meta_json

    return log_event


def ingest_batch(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Send a batch of logs to the DevMesh ingestion API.

    Args:
        logs: List of log events in DevMesh schema

    Returns:
        API response
    """
    url = f"{API_BASE_URL}/ingest/logs"
    payload = {"logs": logs}

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"✗ Failed to ingest batch: {e}")
        return {"ingested": 0, "failed": len(logs), "errors": [str(e)]}


def ship_logs():
    """
    Main function to ship logs from journald to DevMesh.
    """
    print("=" * 80)
    print("DevMesh Platform - Log Shipper")
    print("=" * 80)
    print(f"Node: {NODE_NAME}")
    print(f"API: {API_BASE_URL}")
    print(f"Lookback: {LOOKBACK_HOURS} hours")
    print(f"Batch size: {BATCH_SIZE}")
    print("=" * 80)

    # Fetch logs from journald
    journald_logs = get_journald_logs(since_hours=LOOKBACK_HOURS)

    if not journald_logs:
        print("No logs to ingest")
        return

    # Transform to DevMesh schema
    print(f"\nTransforming {len(journald_logs)} logs...")
    log_events = []
    for entry in journald_logs:
        try:
            log_events.append(transform_journald_to_log_event(entry))
        except Exception as e:
            print(f"Warning: Failed to transform log entry: {e}")
            continue

    print(f"✓ Transformed {len(log_events)} logs")

    # Ingest in batches
    total_ingested = 0
    total_failed = 0
    batch_count = 0

    print(f"\nIngesting in batches of {BATCH_SIZE}...")
    for i in range(0, len(log_events), BATCH_SIZE):
        batch = log_events[i:i + BATCH_SIZE]
        batch_count += 1

        print(f"  Batch {batch_count}: {len(batch)} logs...", end=' ')
        result = ingest_batch(batch)

        total_ingested += result.get('ingested', 0)
        total_failed += result.get('failed', 0)

        print(f"✓ {result.get('ingested', 0)} ingested, {result.get('failed', 0)} failed")

        if result.get('errors'):
            for error in result['errors'][:3]:  # Show first 3 errors
                print(f"    Error: {error}")

    # Summary
    print("\n" + "=" * 80)
    print(f"Ingestion Complete")
    print(f"  Total logs: {len(journald_logs)}")
    print(f"  Transformed: {len(log_events)}")
    print(f"  Ingested: {total_ingested}")
    print(f"  Failed: {total_failed}")
    print(f"  Batches: {batch_count}")
    print("=" * 80)


if __name__ == "__main__":
    try:
        ship_logs()
    except KeyboardInterrupt:
        print("\n\nShipping interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
