#!/usr/bin/env python3
"""DevMesh platform self-monitor.

Runs a fixed set of checks. Each check returns (ok: bool, name, detail).
Failures are logged to syslog at WARNING (so the platform self-ingests)
and written to /tmp/devmesh-health.json for external pickup. Exit code
reflects worst severity: 0 OK, 1 degraded, 2 critical.

Designed to run every ~5 min via systemd timer. Survives DB outage:
DB-side checks fail loudly rather than crashing.
"""
import json
import os
import shutil
import subprocess
import sys
import syslog
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

DISK_WARN_PCT = 80
DISK_CRIT_PCT = 90
INGEST_STALE_WARN_SEC = 300
INGEST_STALE_CRIT_SEC = 1800
FAILED_BATCHES_WARN_BYTES = 50 * 1024 * 1024
FAILED_BATCHES_CRIT_BYTES = 500 * 1024 * 1024
SERVICES = ['devmesh-api.service', 'devmesh-shipper.service']
FAILED_BATCHES = Path(__file__).parent.parent / 'shipper' / 'failed_batches.jsonl'
STATUS_FILE = Path('/tmp/devmesh-health.json')


def check_disk():
    results = []
    for mount in ['/']:
        try:
            usage = shutil.disk_usage(mount)
            pct = usage.used * 100 / usage.total
            free_gb = usage.free / 1e9
            severity = 'crit' if pct >= DISK_CRIT_PCT else 'warn' if pct >= DISK_WARN_PCT else 'ok'
            results.append((severity, f'disk:{mount}', f'{pct:.0f}% used, {free_gb:.1f} GB free'))
        except OSError as e:
            results.append(('crit', f'disk:{mount}', f'stat failed: {e}'))
    return results


def check_services():
    out = []
    for svc in SERVICES:
        rc = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True).returncode
        out.append(('ok' if rc == 0 else 'crit', f'service:{svc}', 'active' if rc == 0 else 'not active'))
    return out


def check_db_and_ingest():
    try:
        from db.database import get_sync_connection
        conn = get_sync_connection()
        cur = conn.cursor()
        cur.execute("SELECT MAX(timestamp) AS mx FROM log_events")
        mx = cur.fetchone()['mx']
        conn.close()
    except Exception as e:
        return [('crit', 'db:connect', f'{type(e).__name__}: {e}')]

    if mx is None:
        return [('crit', 'ingest:staleness', 'no events in log_events')]

    age = (datetime.now() - mx).total_seconds()
    severity = 'crit' if age >= INGEST_STALE_CRIT_SEC else 'warn' if age >= INGEST_STALE_WARN_SEC else 'ok'
    return [
        ('ok', 'db:connect', 'reachable'),
        (severity, 'ingest:staleness', f'last event {age:.0f}s ago ({mx})'),
    ]


def check_failed_batches():
    if not FAILED_BATCHES.exists():
        return [('ok', 'shipper:backlog', 'no failed batches file')]
    size = FAILED_BATCHES.stat().st_size
    severity = 'crit' if size >= FAILED_BATCHES_CRIT_BYTES else 'warn' if size >= FAILED_BATCHES_WARN_BYTES else 'ok'
    return [(severity, 'shipper:backlog', f'{size / 1e6:.1f} MB')]


SEV_RANK = {'ok': 0, 'warn': 1, 'crit': 2}
SEV_EXIT = {'ok': 0, 'warn': 1, 'crit': 2}
SEV_SYSLOG = {'ok': syslog.LOG_INFO, 'warn': syslog.LOG_WARNING, 'crit': syslog.LOG_ERR}


def main():
    syslog.openlog('devmesh-health', syslog.LOG_PID, syslog.LOG_USER)
    results = []
    for fn in (check_disk, check_services, check_db_and_ingest, check_failed_batches):
        try:
            results.extend(fn())
        except Exception as e:
            results.append(('crit', f'check:{fn.__name__}', f'unhandled: {type(e).__name__}: {e}'))

    worst = max((r[0] for r in results), key=lambda s: SEV_RANK[s], default='ok')

    payload = {
        'ts': datetime.now().isoformat(),
        'overall': worst,
        'checks': [{'severity': s, 'name': n, 'detail': d} for s, n, d in results],
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2))

    for severity, name, detail in results:
        line = f'[{severity.upper()}] {name}: {detail}'
        print(line)
        if severity != 'ok':
            syslog.syslog(SEV_SYSLOG[severity], line)

    sys.exit(SEV_EXIT[worst])


if __name__ == '__main__':
    main()
