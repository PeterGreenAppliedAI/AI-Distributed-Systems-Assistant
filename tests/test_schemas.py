"""Tests for Pydantic model validation."""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from models.schemas import (
    LogEventCreate,
    LogIngestRequest,
    LogLevel,
    HealthResponse,
    ErrorResponse,
)


class TestLogLevel:
    def test_all_levels_exist(self):
        expected = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"}
        assert {l.value for l in LogLevel} == expected

    def test_case_sensitive(self):
        with pytest.raises(ValueError):
            LogLevel("info")


class TestLogEventCreate:
    def test_valid_event(self):
        event = LogEventCreate(
            timestamp="2025-12-01T12:00:00Z",
            source="journald",
            service="test.service",
            host="node-1",
            level="INFO",
            message="hello",
        )
        assert event.level == LogLevel.INFO
        assert event.host == "node-1"

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            LogEventCreate(
                timestamp="2025-12-01T12:00:00Z",
                source="journald",
                # missing service
                host="node-1",
                level="INFO",
                message="hello",
            )

    def test_invalid_level(self):
        with pytest.raises(ValidationError):
            LogEventCreate(
                timestamp="2025-12-01T12:00:00Z",
                source="journald",
                service="svc",
                host="node-1",
                level="INVALID",
                message="hello",
            )

    def test_source_max_length(self):
        with pytest.raises(ValidationError):
            LogEventCreate(
                timestamp="2025-12-01T12:00:00Z",
                source="x" * 256,
                service="svc",
                host="node-1",
                level="INFO",
                message="hello",
            )

    def test_optional_fields_default_none(self):
        event = LogEventCreate(
            timestamp="2025-12-01T12:00:00Z",
            source="journald",
            service="svc",
            host="node-1",
            level="INFO",
            message="hello",
        )
        assert event.trace_id is None
        assert event.meta_json is None


class TestLogIngestRequest:
    def test_max_length_enforced(self):
        """M5 - list should reject >10000 items."""
        event = {
            "timestamp": "2025-12-01T12:00:00Z",
            "source": "journald",
            "service": "svc",
            "host": "node-1",
            "level": "INFO",
            "message": "hello",
        }
        with pytest.raises(ValidationError):
            LogIngestRequest(logs=[event] * 10001)

    def test_accepts_valid_batch(self):
        event = {
            "timestamp": "2025-12-01T12:00:00Z",
            "source": "journald",
            "service": "svc",
            "host": "node-1",
            "level": "INFO",
            "message": "hello",
        }
        req = LogIngestRequest(logs=[event])
        assert len(req.logs) == 1


class TestHealthResponse:
    def test_default_timestamp_is_utc(self):
        resp = HealthResponse()
        assert resp.timestamp.tzinfo is not None or resp.status == "ok"


class TestErrorResponse:
    def test_timestamp_generated(self):
        resp = ErrorResponse(error_code="TEST", message="test error")
        assert resp.timestamp is not None
