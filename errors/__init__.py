"""
DevMesh Platform - Domain Errors

Centralized error definitions following API Error Handling Architecture principles:
- Domain Truth: Canonical error codes independent of transport
- Boundary Translation: Mapped to HTTP responses at API layer
- Enforced Consistency: All errors flow through single handler
"""

from .domain import (
    DevMeshError,
    DatabaseError,
    DatabaseConnectionError,
    ValidationError,
    EmptyBatchError,
    IngestionError,
    QueryError,
    ConfigurationError,
)

__all__ = [
    'DevMeshError',
    'DatabaseError',
    'DatabaseConnectionError',
    'ValidationError',
    'EmptyBatchError',
    'IngestionError',
    'QueryError',
    'ConfigurationError',
]
