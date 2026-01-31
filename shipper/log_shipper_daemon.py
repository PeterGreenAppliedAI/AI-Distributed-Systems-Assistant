#!/usr/bin/env python3
"""
DevMesh Platform - Real-Time Log Shipper Daemon
Continuously streams logs from journald to DevMesh API in real-time.

Includes configurable filtering to improve signal-to-noise ratio for LLM analysis.
Filter rules are loaded from filter_config.yaml (schema-validated).
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
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import requests
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

# Import filter module (DRY - single source of truth)
from filter_config import FilterConfig, LogFilter

# Shared transforms (M4)
from transforms import map_priority_to_level, transform_journald_to_log_event

# Configuration
API_HOST = os.getenv('API_HOST', '127.0.0.1')
API_PORT = os.getenv('API_PORT', '8000')
API_BASE_URL = f"http://{API_HOST}:{API_PORT}"
API_KEY = os.getenv('API_KEY', '')  # B1 - optional API key
BATCH_SIZE = int(os.getenv('SHIPPER_BATCH_SIZE', 50))
NODE_NAME = os.getenv('NODE_NAME', 'dev-services')
CURSOR_FILE = os.getenv('SHIPPER_CURSOR_FILE', 'shipper/cursor.txt')
FAILED_BATCHES_FILE = os.getenv('SHIPPER_FAILED_BATCHES_FILE', 'shipper/failed_batches.jsonl')
RETRY_DELAY = 5

# Global flag for graceful shutdown
shutdown_requested = False

# Load filter configuration from YAML (schema-validated)
try:
    filter_config = FilterConfig.load_default()
    log_filter = LogFilter(filter_config)
    print(f"[INFO] Filter config loaded: {len(filter_config.drop_patterns)} patterns")
except Exception as e:
    print(f"[WARN] Failed to load filter config, filtering disabled: {e}")
    filter_config = None
    log_filter = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global shutdown_requested
    print(f"\n[SIGNAL] Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


def _get_request_headers() -> dict:
    """Build HTTP headers, including API key if configured (B1)."""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


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


def _spool_failed_batch(logs: List[Dict[str, Any]]):
    """Write failed batch to dead-letter spool file (H3)."""
    try:
        os.makedirs(os.path.dirname(FAILED_BATCHES_FILE) or '.', exist_ok=True)
        with open(FAILED_BATCHES_FILE, 'a') as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "count": len(logs),
                "logs": logs,
            }) + '\n')
        print(f"[SPOOL] Wrote {len(logs)} logs to dead-letter spool")
    except Exception as e:
        print(f"[ERROR] Failed to write dead-letter spool: {e}")


def _replay_spooled_batches():
    """On startup, attempt to re-ingest any spooled failed batches (H3)."""
    if not os.path.exists(FAILED_BATCHES_FILE):
        return

    print("[INFO] Found failed_batches.jsonl, attempting re-ingestion...")
    remaining = []
    replayed = 0

    try:
        with open(FAILED_BATCHES_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    batch_record = json.loads(line)
                    logs = batch_record.get('logs', [])
                    if ingest_batch(logs):
                        replayed += len(logs)
                    else:
                        remaining.append(line)
                except (json.JSONDecodeError, Exception) as e:
                    print(f"[WARN] Failed to replay spool entry: {e}")
                    remaining.append(line)

        # Rewrite file with only remaining failures
        if remaining:
            with open(FAILED_BATCHES_FILE, 'w') as f:
                f.write('\n'.join(remaining) + '\n')
        else:
            os.remove(FAILED_BATCHES_FILE)

        if replayed:
            print(f"[INFO] Replayed {replayed} spooled logs successfully")
    except Exception as e:
        print(f"[WARN] Error replaying spooled batches: {e}")


def ingest_batch(logs: List[Dict[str, Any]]) -> bool:
    """Send batch of logs to DevMesh API. Returns True if successful."""
    if not logs:
        return True

    url = f"{API_BASE_URL}/ingest/logs"
    payload = {"logs": logs}

    try:
        response = requests.post(url, json=payload, headers=_get_request_headers(), timeout=10)
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
    """Follow journald in real-time and stream logs to DevMesh API."""
    global shutdown_requested

    cursor = load_cursor()

    cmd = [
        'journalctl',
        '--output=json',
        '--follow',
        '--no-pager',
    ]

    if cursor:
        cmd.extend(['--after-cursor', cursor])
    else:
        cmd.extend(['--since', 'now'])

    print(f"[INFO] Starting journald follow...")
    print(f"[INFO] Command: {' '.join(cmd)}")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        batch = []
        last_cursor = None
        log_count = 0

        print(f"[INFO] Streaming logs in real-time (batch size: {BATCH_SIZE})...")
        print("-" * 80)

        for line in iter(process.stdout.readline, ''):
            if shutdown_requested:
                print("[INFO] Shutdown requested, stopping log stream...")
                break

            if not line.strip():
                continue

            try:
                entry = json.loads(line)

                if '__CURSOR' in entry:
                    last_cursor = entry['__CURSOR']

                # Use shared transform (M4)
                log_event = transform_journald_to_log_event(entry, NODE_NAME)

                # Apply filtering
                if log_filter is not None:
                    keep, drop_reason = log_filter.filter_log(log_event)
                    if not keep:
                        continue

                batch.append(log_event)
                log_count += 1

                if log_count % 10 == 0:
                    service = log_event['service']
                    level = log_event['level']
                    message = log_event['message'][:50]
                    print(f"[{log_count:6}] [{level:8}] {service:25} | {message}", flush=True)

                # Send batch when full
                if len(batch) >= BATCH_SIZE:
                    print(f"[BATCH] Sending {len(batch)} logs to API...", flush=True)
                    success = ingest_batch(batch)

                    if success:
                        print(f"[BATCH] Ingested {len(batch)} logs", flush=True)
                        batch = []
                        # M1 - save cursor after each successful batch
                        if last_cursor:
                            save_cursor(last_cursor)
                    else:
                        print(f"[RETRY] Waiting {RETRY_DELAY}s before retry...")
                        time.sleep(RETRY_DELAY)
                        if ingest_batch(batch):
                            batch = []
                            if last_cursor:
                                save_cursor(last_cursor)
                        else:
                            # H3 - spool to dead-letter instead of discarding
                            _spool_failed_batch(batch)
                            batch = []

            except json.JSONDecodeError as e:
                print(f"[WARN] Failed to parse JSON: {e}")
                continue
            except Exception as e:
                print(f"[ERROR] Error processing log entry: {e}")
                continue

        # Send remaining logs
        if batch:
            print(f"[INFO] Sending final batch of {len(batch)} logs...")
            if not ingest_batch(batch):
                _spool_failed_batch(batch)

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
        print(f"[INFO] DevMesh API is healthy")
        return True
    except Exception as e:
        print(f"[ERROR] DevMesh API not reachable: {e}")
        return False


def main():
    """Main daemon entry point"""
    global shutdown_requested

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 80)
    print("DevMesh Platform - Real-Time Log Shipper Daemon")
    print("=" * 80)
    print(f"Node: {NODE_NAME}")
    print(f"API: {API_BASE_URL}")
    print(f"API Auth: {'key configured' if API_KEY else 'none'}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Cursor file: {CURSOR_FILE}")
    if log_filter is not None and filter_config is not None:
        print(f"Filtering: ENABLED (config: filter_config.yaml)")
        print(f"  - {len(filter_config.drop_patterns)} noise patterns")
        print(f"  - {len(filter_config.always_keep_services)} protected services")
        print(f"  - Keeping levels: {', '.join(sorted(filter_config.always_keep_levels))}")
    else:
        print(f"Filtering: DISABLED")
    print("=" * 80)

    if not check_api_health():
        print("[ERROR] Cannot start - DevMesh API is not available")
        print(f"[INFO] Make sure the API is running: cd /home/tadeu718/devmesh-platform && python main.py")
        sys.exit(1)

    # H3 - replay any spooled failed batches from previous runs
    _replay_spooled_batches()

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
    if log_filter is not None:
        print(log_filter.get_stats_summary())
    print("=" * 80)


if __name__ == "__main__":
    main()
