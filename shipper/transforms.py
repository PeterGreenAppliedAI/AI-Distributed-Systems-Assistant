"""
DevMesh Platform - Shared Shipper Transforms (M4)

Shared transform functions used by both log_shipper.py and log_shipper_daemon.py.
Single source of truth for journald-to-DevMesh mapping.
"""

from datetime import datetime, timezone
from typing import Dict, Any


def map_priority_to_level(priority: str) -> str:
    """
    Map journald priority to DevMesh log level.

    Journald priorities (syslog):
    0 - emerg, 1 - alert, 2 - crit, 3 - err, 4 - warning, 5 - notice, 6 - info, 7 - debug
    """
    priority_map = {
        '0': 'FATAL',
        '1': 'CRITICAL',
        '2': 'CRITICAL',
        '3': 'ERROR',
        '4': 'WARN',
        '5': 'INFO',
        '6': 'INFO',
        '7': 'DEBUG',
    }
    return priority_map.get(str(priority), 'INFO')


def transform_journald_to_log_event(entry: Dict[str, Any], node_name: str) -> Dict[str, Any]:
    """
    Transform a journald log entry to DevMesh log_events schema.

    Args:
        entry: Raw journald entry
        node_name: Name of the node this log came from

    Returns:
        Log event in DevMesh schema
    """
    # Extract timestamp (microseconds since epoch)
    timestamp_us = int(entry.get('__REALTIME_TIMESTAMP', '0'))
    timestamp = datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc)

    # Get service/unit name
    service = entry.get('_SYSTEMD_UNIT', entry.get('SYSLOG_IDENTIFIER', 'unknown'))

    # Get message
    message = entry.get('MESSAGE', '')

    # Get priority and map to level
    priority = entry.get('PRIORITY', '6')
    level = map_priority_to_level(priority)

    # Build log event
    log_event = {
        'timestamp': timestamp.isoformat(),
        'source': 'journald',
        'service': service,
        'host': node_name,
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
    if '_HOSTNAME' in entry:
        meta_json['hostname'] = entry['_HOSTNAME']

    if meta_json:
        log_event['meta_json'] = meta_json

    return log_event
