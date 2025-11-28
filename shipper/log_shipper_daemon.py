#!/usr/bin/env python3
"""
DevMesh Platform - Real-Time Log Shipper Daemon
Continuously streams logs from journald to DevMesh API in real-time
"""

import os
import sys

# Force unbuffered output for real-time logging
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)
import json
import subprocess
import time
import signal
from datetime import datetime
from typing import List, Dict, Any, Optional
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
BATCH_SIZE = int(os.getenv('SHIPPER_BATCH_SIZE', 50))  # Smaller batches for lower latency
NODE_NAME = os.getenv('NODE_NAME', 'dev-services')
CURSOR_FILE = os.getenv('SHIPPER_CURSOR_FILE', 'shipper/cursor.txt')
RETRY_DELAY = 5  # seconds to wait before retrying on failure

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global shutdown_requested
    print(f"\n[SIGNAL] Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


def save_cursor(cursor: str):
    """Save journald cursor to file for crash recovery"""
    try:
        os.makedirs(os.path.dirname(CURSOR_FILE), exist_ok=True)
        with open(CURSOR_FILE, 'w') as f:
            f.write(cursor)
    except Exception as e:
        print(f"[WARN] Failed to save cursor: {e}")


def load_cursor() -> Optional[str]:
    """Load last saved journald cursor"""
    try:
        if os.path.exists(CURSOR_FILE):
            with open(CURSOR_FILE, 'r') as f:
                cursor = f.read().strip()
                if cursor:
                    print(f"[INFO] Resuming from saved cursor: {cursor[:50]}...")
                    return cursor
    except Exception as e:
        print(f"[WARN] Failed to load cursor: {e}")
    return None


def map_priority_to_level(priority: str) -> str:
    """Map journald priority to DevMesh log level"""
    priority_map = {
        '0': 'FATAL',
        '1': 'CRITICAL',
        '2': 'CRITICAL',
        '3': 'ERROR',
        '4': 'WARN',
        '5': 'INFO',
        '6': 'INFO',
        '7': 'DEBUG'
    }
    return priority_map.get(str(priority), 'INFO')


def transform_journald_to_log_event(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Transform journald entry to DevMesh log_events schema"""
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
    if '_PID' in entry:
        meta_json['pid'] = entry['_PID']
    if '_COMM' in entry:
        meta_json['comm'] = entry['_COMM']
    if 'SYSLOG_FACILITY' in entry:
        meta_json['facility'] = entry['SYSLOG_FACILITY']

    if meta_json:
        log_event['meta_json'] = meta_json

    return log_event


def ingest_batch(logs: List[Dict[str, Any]]) -> bool:
    """
    Send batch of logs to DevMesh API.
    Returns True if successful, False otherwise.
    """
    if not logs:
        return True

    url = f"{API_BASE_URL}/ingest/logs"
    payload = {"logs": logs}

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()

        ingested = result.get('ingested', 0)
        failed = result.get('failed', 0)

        if failed > 0:
            print(f"[WARN] Batch ingestion partial: {ingested} ingested, {failed} failed")

        return True

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to ingest batch: {e}")
        return False


def follow_journald():
    """
    Follow journald in real-time and stream logs to DevMesh API.

    Uses journalctl -f to continuously tail new log entries.
    """
    global shutdown_requested

    # Load saved cursor if exists
    cursor = load_cursor()

    # Build journalctl command
    cmd = [
        'journalctl',
        '--output=json',
        '--follow',
        '--no-pager'
    ]

    # If we have a saved cursor, resume from there
    if cursor:
        cmd.extend(['--after-cursor', cursor])
    else:
        # Otherwise, start from now (don't re-ingest old logs)
        cmd.extend(['--since', 'now'])

    print(f"[INFO] Starting journald follow...")
    print(f"[INFO] Command: {' '.join(cmd)}")

    try:
        # Start journalctl subprocess
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # Line buffered
        )

        batch = []
        last_cursor = None
        cursor_save_interval = 100  # Save cursor every N logs
        log_count = 0

        print(f"[INFO] Streaming logs in real-time (batch size: {BATCH_SIZE})...")
        print("-" * 80)

        # Read logs line by line as they come in
        for line in iter(process.stdout.readline, ''):
            if shutdown_requested:
                print("[INFO] Shutdown requested, stopping log stream...")
                break

            if not line.strip():
                continue

            try:
                # Parse JSON log entry
                entry = json.loads(line)

                # Update cursor
                if '__CURSOR' in entry:
                    last_cursor = entry['__CURSOR']

                # Transform to DevMesh schema
                log_event = transform_journald_to_log_event(entry)
                batch.append(log_event)
                log_count += 1

                # Print progress (every 10th log to reduce noise)
                if log_count % 10 == 0:
                    service = log_event['service']
                    level = log_event['level']
                    message = log_event['message'][:50]
                    print(f"[{log_count:6}] [{level:8}] {service:25} | {message}", flush=True)

                # Send batch when full (for efficiency)
                if len(batch) >= BATCH_SIZE:
                    print(f"[BATCH] Sending {len(batch)} logs to API...", flush=True)
                    success = ingest_batch(batch)

                    if success:
                        print(f"[BATCH] ✓ Ingested {len(batch)} logs", flush=True)
                        batch = []
                        # Save cursor periodically
                        if last_cursor and log_count % cursor_save_interval == 0:
                            save_cursor(last_cursor)
                    else:
                        # If ingestion failed, wait and retry
                        print(f"[RETRY] Waiting {RETRY_DELAY}s before retry...")
                        time.sleep(RETRY_DELAY)
                        # Try again
                        if ingest_batch(batch):
                            batch = []
                        else:
                            print("[ERROR] Retry failed, discarding batch to avoid blocking")
                            batch = []

            except json.JSONDecodeError as e:
                print(f"[WARN] Failed to parse JSON: {e}")
                continue
            except Exception as e:
                print(f"[ERROR] Error processing log entry: {e}")
                continue

        # Send any remaining logs in batch
        if batch:
            print(f"[INFO] Sending final batch of {len(batch)} logs...")
            ingest_batch(batch)

        # Save final cursor
        if last_cursor:
            save_cursor(last_cursor)

        process.terminate()
        process.wait(timeout=5)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    except Exception as e:
        print(f"[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if last_cursor:
            save_cursor(last_cursor)
        print(f"\n[INFO] Total logs processed: {log_count}")


def check_api_health():
    """Check if DevMesh API is reachable"""
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        response.raise_for_status()
        print(f"[INFO] ✓ DevMesh API is healthy")
        return True
    except Exception as e:
        print(f"[ERROR] ✗ DevMesh API not reachable: {e}")
        return False


def main():
    """Main daemon entry point"""
    global shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 80)
    print("DevMesh Platform - Real-Time Log Shipper Daemon")
    print("=" * 80)
    print(f"Node: {NODE_NAME}")
    print(f"API: {API_BASE_URL}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Cursor file: {CURSOR_FILE}")
    print("=" * 80)

    # Check API health before starting
    if not check_api_health():
        print("[ERROR] Cannot start - DevMesh API is not available")
        print(f"[INFO] Make sure the API is running: cd /home/tadeu718/devmesh-platform && python main.py")
        sys.exit(1)

    # Start streaming
    while not shutdown_requested:
        try:
            follow_journald()
        except Exception as e:
            print(f"[ERROR] Stream crashed: {e}")
            if not shutdown_requested:
                print(f"[RETRY] Restarting in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

    print("\n" + "=" * 80)
    print("DevMesh Log Shipper Daemon stopped")
    print("=" * 80)


if __name__ == "__main__":
    main()
