"""
DevMesh Platform - Pydantic Models
Data schemas for API requests and responses
"""

from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    """Log levels supported by DevMesh Platform"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


class LogEventCreate(BaseModel):
    """
    Schema for creating a log event via POST /ingest/logs
    """
    timestamp: datetime = Field(..., description="When the log event occurred (UTC)")
    source: str = Field(..., description="Component or exporter name", max_length=255)
    service: str = Field(..., description="Logical service name", max_length=255)
    host: str = Field(..., description="Node or VM name", max_length=255)
    level: LogLevel = Field(..., description="Log severity level")
    message: str = Field(..., description="Log message content")

    # Optional fields
    trace_id: Optional[str] = Field(None, description="Distributed trace ID", max_length=64)
    span_id: Optional[str] = Field(None, description="Span ID within trace", max_length=32)
    event_type: Optional[str] = Field(None, description="Event type (e.g. http_request, db_error)", max_length=100)
    error_code: Optional[str] = Field(None, description="Error code (e.g. ECONNRESET, HTTP_500)", max_length=50)
    meta_json: Optional[Dict[str, Any]] = Field(None, description="Extra metadata as JSON")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-11-28T18:00:00.000000Z",
                "source": "loki",
                "service": "loki.service",
                "host": "dev-services",
                "level": "INFO",
                "message": "uploading tables",
                "event_type": "table_upload",
                "meta_json": {"index_store": "boltdb-shipper-2020-10-24"}
            }
        }


class LogEventResponse(LogEventCreate):
    """
    Schema for log event responses (includes database ID)
    """
    id: int = Field(..., description="Database auto-increment ID")

    class Config:
        from_attributes = True


class LogIngestRequest(BaseModel):
    """
    Batch log ingestion request
    """
    logs: list[LogEventCreate] = Field(..., description="List of log events to ingest")


class LogIngestResponse(BaseModel):
    """
    Response for log ingestion (idempotent)
    """
    ingested: int = Field(..., description="Number of logs successfully ingested")
    duplicates: int = Field(0, description="Number of duplicate logs skipped")
    failed: int = Field(0, description="Number of logs that failed")
    errors: Optional[list[str]] = Field(None, description="Error messages if any")


class LogQueryParams(BaseModel):
    """
    Query parameters for GET /query/logs
    """
    service: Optional[str] = Field(None, description="Filter by service name")
    host: Optional[str] = Field(None, description="Filter by host name")
    level: Optional[LogLevel] = Field(None, description="Filter by log level")
    start_time: Optional[datetime] = Field(None, description="Start of time window (UTC)")
    end_time: Optional[datetime] = Field(None, description="End of time window (UTC)")
    limit: int = Field(100, description="Maximum number of logs to return", ge=1, le=10000)
    offset: int = Field(0, description="Number of logs to skip", ge=0)


class HealthResponse(BaseModel):
    """Response for /health endpoint"""
    status: str = Field("ok", description="Health status")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Current server time (UTC)")


class InfoResponse(BaseModel):
    """Response for /info endpoint"""
    name: str = Field("DevMesh Platform", description="Project name")
    version: str = Field("0.1.0", description="API version")
    description: str = Field(
        "AI-Native Observability Platform for Local Infrastructure",
        description="Project description"
    )
    node: str = Field(..., description="Node this API is running on")


class ErrorResponse(BaseModel):
    """
    Standardized error response format.

    All API errors return this structure for consistency.
    Follows API Error Handling Architecture principles.
    """
    error_code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="When error occurred")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error context")

    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "VALIDATION_ERROR",
                "message": "No logs provided in request",
                "timestamp": "2025-12-23T15:00:00.000000Z",
                "details": {"field": "logs"}
            }
        }
