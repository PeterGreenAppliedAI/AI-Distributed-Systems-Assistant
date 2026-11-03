#!/usr/bin/env python3
"""Drain shipper/failed_batches.jsonl back through the ingest API.

Each line is one batch: {"timestamp": ..., "count": N, "logs": [...]}
We POST each batch's logs array to /ingest/logs, sleep briefly between
batches, and track progress so re-runs resume rather than restart.

To avoid racing a shipper that appends new failures while we drain, the
spool is first renamed to <file>.draining; the shipper then starts a fresh
failed_batches.jsonl, which a later run picks up. The .draining file is
streamed line by line (spools can be hundreds of MB).

On reaching the end of the file the .draining file is renamed to .drained
and the progress file is removed. Lines with bad JSON are copied to
<file>.rejected so nothing is silently lost.

If API_AUTH_ENABLED is on, set API_KEY (env or .env) — it is sent as X-API-Key.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DEFAULT_FILE = ROOT / 'shipper' / 'failed_batches.jsonl'
DEFAULT_API = 'http://localhost:8000/ingest/logs'

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass


def _post(session, url, logs, timeout):
    return session.post(url, json={'logs': logs}, timeout=timeout)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', type=Path, default=DEFAULT_FILE)
    p.add_argument('--api', default=DEFAULT_API)
    p.add_argument('--api-key', default=os.getenv('API_KEY'),
                   help='X-API-Key value (default: $API_KEY)')
    p.add_argument('--sleep', type=float, default=0.05,
                   help='seconds between batches (rate limit)')
    p.add_argument('--limit', type=int, default=0,
                   help='stop after N batches (0 = no limit)')
    p.add_argument('--timeout', type=float, default=30.0,
                   help='per-request timeout seconds')
    args = p.parse_args()

    draining = args.file.with_suffix(args.file.suffix + '.draining')
    progress_file = draining.with_suffix(draining.suffix + '.progress')
    rejected_file = args.file.with_suffix(args.file.suffix + '.rejected')

    if draining.exists():
        print(f'resuming existing {draining}')
    elif args.file.exists():
        args.file.rename(draining)
        print(f'moved {args.file} -> {draining} (shipper will start a fresh spool)')
    else:
        print(f'no file at {args.file}', file=sys.stderr)
        return 1

    start_idx = 0
    if progress_file.exists():
        start_idx = int(progress_file.read_text().strip() or 0)
        print(f'resuming from line {start_idx}')

    session = requests.Session()
    if args.api_key:
        session.headers['X-API-Key'] = args.api_key

    sent_batches = 0
    sent_logs = 0
    rejected = 0
    reached_end = True
    next_idx = start_idx
    t0 = time.time()
    last_log = t0

    with draining.open() as f:
        for i, line in enumerate(f):
            if i < start_idx:
                continue
            if args.limit and sent_batches >= args.limit:
                reached_end = False
                break

            next_idx = i + 1
            if not line.strip():
                continue
            try:
                batch = json.loads(line)
            except json.JSONDecodeError as e:
                print(f'line {i}: bad JSON ({e}) — copied to {rejected_file}',
                      file=sys.stderr)
                with rejected_file.open('a') as rf:
                    rf.write(line if line.endswith('\n') else line + '\n')
                rejected += 1
                continue

            logs = batch.get('logs') or []
            if not logs:
                continue

            try:
                r = _post(session, args.api, logs, args.timeout)
            except requests.RequestException as e:
                print(f'line {i}: network error: {e} — pausing 5s and retrying once',
                      file=sys.stderr)
                time.sleep(5)
                try:
                    r = _post(session, args.api, logs, args.timeout)
                except requests.RequestException as e2:
                    print(f'line {i}: retry failed: {e2} — stopping', file=sys.stderr)
                    progress_file.write_text(str(i))
                    return 2

            if r.status_code >= 400:
                hint = ' (set API_KEY / --api-key?)' if r.status_code == 401 else ''
                print(f'line {i}: HTTP {r.status_code}{hint}: {r.text[:200]} — stopping',
                      file=sys.stderr)
                progress_file.write_text(str(i))
                return 2

            sent_batches += 1
            sent_logs += len(logs)

            now = time.time()
            if now - last_log > 5.0:
                rate = sent_logs / (now - t0)
                print(f'  [line {i+1}] sent {sent_batches} batches '
                      f'({sent_logs:,} logs) {rate:.0f} logs/s')
                last_log = now

            if sent_batches % 100 == 0:
                progress_file.write_text(str(next_idx))

            if args.sleep:
                time.sleep(args.sleep)

    if reached_end:
        drained = args.file.with_suffix(args.file.suffix + '.drained')
        if drained.exists():  # never clobber an earlier drained spool
            drained = drained.with_suffix(drained.suffix + time.strftime('.%Y%m%dT%H%M%S'))
        draining.rename(drained)
        progress_file.unlink(missing_ok=True)
        print(f'\ndrained {sent_batches} batches ({sent_logs:,} logs), '
              f'{rejected} rejected, in {time.time()-t0:.0f}s — moved to {drained}')
    else:
        progress_file.write_text(str(next_idx))
        print(f'\nstopped at line {next_idx} after --limit '
              f'(progress saved to {progress_file})')

    return 0


if __name__ == '__main__':
    sys.exit(main())
