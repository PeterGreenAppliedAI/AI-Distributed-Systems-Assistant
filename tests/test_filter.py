"""Tests for log filter configuration and pattern matching."""

import pytest
from shipper.filter_config import FilterConfig, LogFilter, DropPattern


@pytest.fixture
def sample_config():
    """Build a minimal filter config for testing."""
    return FilterConfig(
        enabled=True,
        always_keep_levels={"ERROR", "CRITICAL", "FATAL"},
        always_keep_services={"sshd.service", "docker.service"},
        drop_patterns=[
            DropPattern(
                name="test_noise",
                pattern=r"table manager .* uploading tables",
                reason="noisy loki pattern",
            ),
        ],
    )


@pytest.fixture
def log_filter(sample_config):
    return LogFilter(sample_config)


class TestDropPattern:
    def test_matches_positive(self):
        p = DropPattern(name="test", pattern=r"foo.*bar", reason="test")
        assert p.matches("foo baz bar")

    def test_matches_negative(self):
        p = DropPattern(name="test", pattern=r"foo.*bar", reason="test")
        assert not p.matches("hello world")

    def test_invalid_regex_raises(self):
        with pytest.raises(ValueError):
            DropPattern(name="bad", pattern=r"[invalid", reason="test")


class TestFilterConfig:
    def test_from_dict_valid(self):
        cfg = FilterConfig.from_dict({
            "enabled": True,
            "always_keep_levels": ["ERROR"],
            "always_keep_services": ["sshd.service"],
            "drop_patterns": [
                {"name": "p1", "pattern": "noise", "reason": "test"},
            ],
        })
        assert cfg.enabled
        assert "ERROR" in cfg.always_keep_levels

    def test_from_dict_missing_field(self):
        with pytest.raises(ValueError, match="Missing required fields"):
            FilterConfig.from_dict({"enabled": True})

    def test_from_dict_invalid_pattern(self):
        with pytest.raises(ValueError):
            FilterConfig.from_dict({
                "enabled": True,
                "always_keep_levels": [],
                "always_keep_services": [],
                "drop_patterns": [
                    {"name": "bad", "pattern": "[invalid", "reason": "test"},
                ],
            })


class TestLogFilter:
    def test_keeps_important_levels(self, log_filter):
        keep, reason = log_filter.filter_log({
            "level": "ERROR",
            "service": "random.service",
            "message": "table manager test uploading tables",
        })
        assert keep is True

    def test_keeps_protected_services(self, log_filter):
        keep, reason = log_filter.filter_log({
            "level": "INFO",
            "service": "sshd.service",
            "message": "table manager test uploading tables",
        })
        assert keep is True

    def test_drops_noise_pattern(self, log_filter):
        keep, reason = log_filter.filter_log({
            "level": "INFO",
            "service": "loki.service",
            "message": "table manager 2024-01-01 uploading tables",
        })
        assert keep is False
        assert reason == "test_noise"

    def test_keeps_non_matching(self, log_filter):
        keep, reason = log_filter.filter_log({
            "level": "INFO",
            "service": "myapp.service",
            "message": "normal log message",
        })
        assert keep is True

    def test_disabled_filter_keeps_all(self, sample_config):
        sample_config.enabled = False
        lf = LogFilter(sample_config)
        keep, _ = lf.filter_log({
            "level": "INFO",
            "service": "loki.service",
            "message": "table manager test uploading tables",
        })
        assert keep is True

    def test_stats_tracking(self, log_filter):
        log_filter.filter_log({"level": "INFO", "service": "x", "message": "table manager y uploading tables"})
        log_filter.filter_log({"level": "INFO", "service": "x", "message": "normal"})
        stats = log_filter.stats
        assert stats["total_seen"] == 2
        assert stats["total_dropped"] == 1

    def test_stats_summary(self, log_filter):
        log_filter.filter_log({"level": "INFO", "service": "x", "message": "normal"})
        summary = log_filter.get_stats_summary()
        assert "kept" in summary
