"""
DevMesh Platform - Domain Error Types

These errors represent business logic failures independent of transport layer.
Each error has:
- error_code: Machine-readable identifier for programmatic handling
- http_status: Suggested HTTP status (used by error handler, not hardcoded in routes)
- message: Human-readable description

Principles applied:
- Domain Truth: Error semantics defined here, not in routes
- Contracts Everywhere: Consistent structure for all errors
- Auditability: Error codes enable tracking and analysis
"""

from typing import Optional, Dict, Any


class DevMeshError(Exception):
    """
    Base exception for all DevMesh domain errors.

    All domain errors inherit from this class, enabling:
    - Centralized exception handling
    - Consistent error response format
    - Clear separation from framework exceptions
    """
    error_code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for JSON response."""
        result = {
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# =============================================================================
# Database Errors
# =============================================================================

class DatabaseError(DevMeshError):
    """Base class for database-related errors."""
    error_code = "DATABASE_ERROR"
    http_status = 503  # Service Unavailable


class DatabaseConnectionError(DatabaseError):
    """Failed to connect to database."""
    error_code = "DATABASE_CONNECTION_FAILED"
    http_status = 503


class IngestionError(DatabaseError):
    """Failed to ingest logs into database."""
    error_code = "INGESTION_FAILED"
    http_status = 500

    def __init__(self, message: str, ingested: int = 0, failed: int = 0,
                 errors: Optional[list] = None):
        details = {
            "ingested": ingested,
            "failed": failed,
        }
        if errors:
            details["errors"] = errors[:10]  # Limit to first 10 errors
        super().__init__(message, details)


class QueryError(DatabaseError):
    """Failed to query logs from database."""
    error_code = "QUERY_FAILED"
    http_status = 500


# =============================================================================
# Validation Errors
# =============================================================================

class ValidationError(DevMeshError):
    """Input validation failed."""
    error_code = "VALIDATION_ERROR"
    http_status = 400  # Bad Request

    def __init__(self, message: str, field: Optional[str] = None):
        details = {"field": field} if field else {}
        super().__init__(message, details)


class EmptyBatchError(ValidationError):
    """Empty batch provided for ingestion."""
    error_code = "EMPTY_BATCH"
    http_status = 400

    def __init__(self):
        super().__init__("No logs provided in request")


# =============================================================================
# Configuration Errors
# =============================================================================

class ConfigurationError(DevMeshError):
    """System configuration error."""
    error_code = "CONFIGURATION_ERROR"
    http_status = 500

    def __init__(self, message: str, config_key: Optional[str] = None):
        details = {"config_key": config_key} if config_key else {}
        super().__init__(message, details)
