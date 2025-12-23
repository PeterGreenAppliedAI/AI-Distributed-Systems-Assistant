"""
DevMesh Log Filter Configuration Module

Provides schema-validated filter configuration loaded from YAML.
Single source of truth for filter rules - used by shipper and test tools.

Principles applied:
- Single Responsibility: Only handles filter config loading and validation
- Contracts Everywhere: Pydantic schema enforces structure
- DRY: Single module used by all consumers
- Open/Closed: Add new patterns via YAML, not code changes
"""

import re
import os
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple, Optional
from dataclasses import dataclass, field

import yaml


# Schema validation using dataclasses (lightweight, no extra deps)
@dataclass
class DropPattern:
    """A single drop pattern with metadata for auditability."""
    name: str
    pattern: str
    reason: str
    _compiled: Optional[re.Pattern] = field(default=None, repr=False)

    def __post_init__(self):
        """Compile regex pattern on initialization."""
        try:
            self._compiled = re.compile(self.pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{self.pattern}' in {self.name}: {e}")

    def matches(self, message: str) -> bool:
        """Check if message matches this pattern."""
        return bool(self._compiled.search(message))


@dataclass
class FilterConfig:
    """
    Complete filter configuration with validation.

    Attributes:
        enabled: Master switch for filtering
        always_keep_levels: Log levels to never filter
        always_keep_services: Services to never filter
        drop_patterns: List of patterns to drop
    """
    enabled: bool
    always_keep_levels: Set[str]
    always_keep_services: Set[str]
    drop_patterns: List[DropPattern]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FilterConfig':
        """Create FilterConfig from dictionary (parsed YAML)."""
        # Validate required fields
        required = ['enabled', 'always_keep_levels', 'always_keep_services', 'drop_patterns']
        missing = [f for f in required if f not in data]
        if missing:
            raise ValueError(f"Missing required fields in filter config: {missing}")

        # Parse drop patterns
        patterns = []
        for i, p in enumerate(data['drop_patterns']):
            if not all(k in p for k in ['name', 'pattern', 'reason']):
                raise ValueError(f"Drop pattern {i} missing required fields (name, pattern, reason)")
            patterns.append(DropPattern(
                name=p['name'],
                pattern=p['pattern'],
                reason=p['reason']
            ))

        return cls(
            enabled=bool(data['enabled']),
            always_keep_levels=set(data['always_keep_levels']),
            always_keep_services=set(data['always_keep_services']),
            drop_patterns=patterns
        )

    @classmethod
    def from_yaml(cls, path: Path) -> 'FilterConfig':
        """Load and validate config from YAML file."""
        if not path.exists():
            raise FileNotFoundError(f"Filter config not found: {path}")

        with open(path, 'r') as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data)

    @classmethod
    def load_default(cls) -> 'FilterConfig':
        """Load config from default location (shipper/filter_config.yaml)."""
        default_path = Path(__file__).parent / 'filter_config.yaml'
        return cls.from_yaml(default_path)


class LogFilter:
    """
    Applies filter rules to log events.

    Thread-safe, stateless filter evaluation.
    Tracks statistics for observability.
    """

    def __init__(self, config: FilterConfig):
        self.config = config
        self._stats = {
            'total_seen': 0,
            'total_dropped': 0,
            'dropped_by_pattern': {}
        }

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def stats(self) -> Dict[str, Any]:
        return self._stats.copy()

    def should_drop(self, log_event: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determine if a log should be dropped.

        Args:
            log_event: Dict with 'level', 'service', 'message' keys

        Returns:
            Tuple of (should_drop: bool, reason: str)
            reason is pattern name if dropped, empty string if kept
        """
        if not self.config.enabled:
            return False, ""

        level = log_event.get('level', 'INFO')
        service = log_event.get('service', '')
        message = log_event.get('message', '')

        # Rule 1: Always keep important log levels
        if level in self.config.always_keep_levels:
            return False, ""

        # Rule 2: Always keep logs from protected services
        if service in self.config.always_keep_services:
            return False, ""

        # Rule 3: Drop logs matching noise patterns
        for pattern in self.config.drop_patterns:
            if pattern.matches(message):
                return True, pattern.name

        # Rule 4: Keep everything else
        return False, ""

    def filter_log(self, log_event: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Filter a log and update statistics.

        Returns:
            Tuple of (keep: bool, reason: str)
        """
        self._stats['total_seen'] += 1

        should_drop, reason = self.should_drop(log_event)

        if should_drop:
            self._stats['total_dropped'] += 1
            self._stats['dropped_by_pattern'][reason] = \
                self._stats['dropped_by_pattern'].get(reason, 0) + 1
            return False, reason

        return True, ""

    def get_stats_summary(self) -> str:
        """Return human-readable stats summary."""
        if self._stats['total_seen'] == 0:
            return "No logs processed yet"

        kept = self._stats['total_seen'] - self._stats['total_dropped']
        drop_rate = (self._stats['total_dropped'] / self._stats['total_seen']) * 100

        return (
            f"Filter stats: {kept:,} kept, {self._stats['total_dropped']:,} dropped "
            f"({drop_rate:.1f}% noise reduction)"
        )


# Module-level convenience functions
_default_filter: Optional[LogFilter] = None


def get_default_filter() -> LogFilter:
    """Get or create the default filter instance."""
    global _default_filter
    if _default_filter is None:
        config = FilterConfig.load_default()
        _default_filter = LogFilter(config)
    return _default_filter


def should_drop_log(log_event: Dict[str, Any]) -> Tuple[bool, str]:
    """Convenience function using default filter."""
    return get_default_filter().should_drop(log_event)
