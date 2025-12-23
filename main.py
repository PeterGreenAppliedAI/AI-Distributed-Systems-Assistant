"""
DevMesh Platform - Main FastAPI Application
AI-Native Observability for Local Infrastructure
"""

import os
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from models.schemas import HealthResponse, InfoResponse, ErrorResponse
from db.database import test_connection
from api.routes import router as api_router
from errors import DevMeshError

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI app.
    Runs on startup and shutdown.
    """
    # Startup
    logger.info("=" * 80)
    logger.info("DevMesh Platform Starting")
    logger.info("=" * 80)

    # Test database connection
    if test_connection():
        logger.info("✓ Database connection successful")
    else:
        logger.error("✗ Database connection failed")

    logger.info(f"API running on http://{os.getenv('API_HOST', '0.0.0.0')}:{os.getenv('API_PORT', 8000)}")
    logger.info("=" * 80)

    yield

    # Shutdown
    logger.info("DevMesh Platform shutting down...")


# Create FastAPI app
app = FastAPI(
    title=os.getenv('API_TITLE', 'DevMesh Platform - Observability API'),
    version=os.getenv('API_VERSION', '0.1.0'),
    description="AI-Native Observability Platform for Local Infrastructure",
    lifespan=lifespan
)

# Include API routes
app.include_router(api_router)


# =============================================================================
# Centralized Error Handling
# =============================================================================

@app.exception_handler(DevMeshError)
async def domain_error_handler(request: Request, exc: DevMeshError):
    """
    Centralized handler for all domain errors.

    Translates domain errors to HTTP responses with consistent format.
    This is the single choke point for all error responses.
    """
    logger.error(f"Domain error: {exc.error_code} - {exc.message}")
    if exc.details:
        logger.error(f"  Details: {exc.details}")

    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details if exc.details else None
        ).model_dump(mode='json')
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    """
    Catch-all handler for unexpected exceptions.

    Logs the full error but returns generic message to client.
    Prevents leaking internal details.
    """
    logger.exception(f"Unexpected error on {request.method} {request.url.path}")

    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred"
        ).model_dump(mode='json')
    )


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    Log every request with path and status code
    """
    # Log incoming request
    logger.info(f"→ {request.method} {request.url.path}")

    # Process request
    response = await call_next(request)

    # Log response
    status_emoji = "✓" if response.status_code < 400 else "✗"
    logger.info(f"{status_emoji} {request.method} {request.url.path} → {response.status_code}")

    return response


# Health endpoint
@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    tags=["System"]
)
async def health_check():
    """
    Health check endpoint.
    Returns OK if the API is running.
    """
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow()
    )


# Info endpoint
@app.get(
    "/info",
    response_model=InfoResponse,
    status_code=status.HTTP_200_OK,
    tags=["System"]
)
async def get_info():
    """
    Get platform information.
    Returns static info about the platform.
    """
    return InfoResponse(
        name=os.getenv('API_TITLE', 'DevMesh Platform'),
        version=os.getenv('API_VERSION', '0.1.0'),
        description="AI-Native Observability Platform for Local Infrastructure",
        node=os.getenv('NODE_NAME', 'unknown')
    )


# Root endpoint
@app.get("/", tags=["System"])
async def root():
    """
    Root endpoint - redirects to /info
    """
    return {
        "message": "DevMesh Platform API",
        "version": os.getenv('API_VERSION', '0.1.0'),
        "endpoints": {
            "health": "/health",
            "info": "/info",
            "docs": "/docs",
            "openapi": "/openapi.json"
        }
    }


if __name__ == "__main__":
    import uvicorn

    # Run the FastAPI app
    uvicorn.run(
        "main:app",
        host=os.getenv('API_HOST', '0.0.0.0'),
        port=int(os.getenv('API_PORT', 8000)),
        reload=True,  # Auto-reload on code changes
        log_level="info"
    )
