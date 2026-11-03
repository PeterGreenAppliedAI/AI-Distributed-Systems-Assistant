#!/usr/bin/env python3
"""Drain shipper/failed_batches.jsonl back through the ingest API.

Each line is one batch: {"timestamp": ..., "count": N, "logs": [...]}
We POST each batch's logs array to /ingest/logs, sleep briefly between
batches, and track progress so re-runs resume rather than restart.

On full success the original file is renamed with a .drained suffix and
the progress file is removed. On partial success the progress file
records the last successful line index.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DEFAULT_FILE = ROOT / 'shipper' / 'failed_batches.jsonl'
DEFAULT_API = 'http://localhost:8000/ingest/logs'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', type=Path, default=DEFAULT_FILE)
    p.add_argument('--api', default=DEFAULT_API)
    p.add_argument('--sleep', type=float, default=0.05,
                   help='seconds between batches (rate limit)')
    p.add_argument('--limit', type=int, default=0,
                   help='stop after N batches (0 = no limit)')
    p.add_argument('--timeout', type=float, default=30.0,
                   help='per-request timeout seconds')
    args = p.parse_args()

    if not args.file.exists():
        print(f'no file at {args.file}', file=sys.stderr)
        return 1

    progress_file = args.file.with_suffix(args.file.suffix + '.progress')
    start_idx = 0
    if progress_file.exists():
        start_idx = int(progress_file.read_text().strip() or 0)
        print(f'resuming from line {start_idx}')

    with args.file.open() as f:
        lines = f.readlines()
    total = len(lines)
    print(f'{args.file}: {total} batches, starting at {start_idx}')

    sent_batches = 0
    sent_logs = 0
    failed = 0
    t0 = time.time()
    last_log = t0

    for i in range(start_idx, total):
        if args.limit and sent_batches >= args.limit:
            break

        try:
            batch = json.loads(lines[i])
        except json.JSONDecodeError as e:
            print(f'line {i}: skip — bad JSON: {e}', file=sys.stderr)
            failed += 1
            continue

        logs = batch.get('logs') or []
        if not logs:
            progress_file.write_text(str(i + 1))
            continue

        try:
            r = requests.post(args.api, json={'logs': logs}, timeout=args.timeout)
        except requests.RequestException as e:
            print(f'line {i}: network error: {e} — pausing 5s and retrying once',
                  file=sys.stderr)
            time.sleep(5)
            try:
                r = requests.post(args.api, json={'logs': logs}, timeout=args.timeout)
            except requests.RequestException as e2:
                print(f'line {i}: retry failed: {e2} — stopping', file=sys.stderr)
                progress_file.write_text(str(i))
                return 2

        if r.status_code >= 400:
            print(f'line {i}: HTTP {r.status_code}: {r.text[:200]} — stopping',
                  file=sys.stderr)
            progress_file.write_text(str(i))
            return 2

        sent_batches += 1
        sent_logs += len(logs)

        # Periodic progress update
        now = time.time()
        if now - last_log > 5.0:
            rate = sent_logs / (now - t0)
            eta = (total - i - 1) / max(rate / 50, 1)
            print(f'  [{i+1}/{total}] sent {sent_batches} batches '
                  f'({sent_logs:,} logs) {rate:.0f} logs/s eta ~{eta:.0f}s')
            last_log = now

        if (i + 1) % 100 == 0:
            progress_file.write_text(str(i + 1))

        if args.sleep:
            time.sleep(args.sleep)

    # Mark fully drained if we made it to the end
    if sent_batches > 0 and (start_idx + sent_batches) >= total - failed:
        drained = args.file.with_suffix(args.file.suffix + '.drained')
        args.file.rename(drained)
        progress_file.unlink(missing_ok=True)
        print(f'\ndrained {sent_batches} batches ({sent_logs:,} logs) in '
              f'{time.time()-t0:.0f}s — moved to {drained}')
    else:
        progress_file.write_text(str(start_idx + sent_batches))
        print(f'\nstopped at {start_idx + sent_batches}/{total} '
              f'(progress saved to {progress_file})')

    return 0


if __name__ == '__main__':
    sys.exit(main())
