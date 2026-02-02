"""
DevMesh Platform - Main FastAPI Application
AI-Native Observability for Local Infrastructure
"""

import os
import json
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from models.schemas import HealthResponse, InfoResponse, ErrorResponse
from db.database import init_pool, close_pool, async_test_connection, test_connection
from api.routes import router as api_router
from api.auth import APIKeyMiddleware
from errors import DevMeshError

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------------
# Structured JSON logging (N1)
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


handler = logging.StreamHandler()
handler.setFormatter(_JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI app."""
    logger.info("DevMesh Platform Starting")

    # Initialise async DB pool (H1, H2)
    try:
        await init_pool()
        logger.info("Database pool initialised")
    except Exception as e:
        logger.error("Failed to initialise DB pool: %s", e)

    # Create shared httpx client for embedding service
    embedding_timeout = int(os.getenv('EMBEDDING_TIMEOUT', '120'))
    app.state.http_client = httpx.AsyncClient(timeout=embedding_timeout)
    logger.info("HTTP client initialised (timeout=%ds)", embedding_timeout)

    # Warm template cache from DB
    from services.template_cache import TemplateCache
    app.state.template_cache = TemplateCache()
    try:
        from db.database import get_pool as _get_pool
        pool = _get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name = 'log_templates'
                """)
                row = await cursor.fetchone()
                if row['cnt'] > 0:
                    await cursor.execute("SELECT template_hash, id FROM log_templates")
                    rows = await cursor.fetchall()
                    app.state.template_cache.warm(rows)
                else:
                    logger.info("log_templates table not found, template cache empty")
    except Exception as e:
        logger.warning("Failed to warm template cache: %s", e)

    logger.info("API running on http://%s:%s",
                os.getenv('API_HOST', '0.0.0.0'), os.getenv('API_PORT', 8000))

    yield

    # Shutdown
    await app.state.http_client.aclose()
    logger.info("HTTP client closed")
    await close_pool()
    logger.info("DevMesh Platform shut down")


# Create FastAPI app
app = FastAPI(
    title=os.getenv('API_TITLE', 'DevMesh Platform - Observability API'),
    version=os.getenv('API_VERSION', '0.1.0'),
    description="AI-Native Observability Platform for Local Infrastructure",
    lifespan=lifespan,
)

# Register API key auth middleware (B1)
app.add_middleware(APIKeyMiddleware)

# Include API routes
app.include_router(api_router)


# =============================================================================
# Centralized Error Handling
# =============================================================================

@app.exception_handler(DevMeshError)
async def domain_error_handler(request: Request, exc: DevMeshError):
    logger.error("Domain error: %s - %s", exc.error_code, exc.message)
    if exc.details:
        logger.error("  Details: %s", exc.details)

    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details if exc.details else None,
        ).model_dump(mode='json'),
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception("Unexpected error on %s %s", request.method, request.url.path)

    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred",
        ).model_dump(mode='json'),
    )


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info("-> %s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("<- %s %s %d", request.method, request.url.path, response.status_code)
    return response


# Health endpoint (N2 - pings DB, returns degraded if down)
@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    tags=["System"],
)
async def health_check():
    db_ok = await async_test_connection()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        timestamp=datetime.now(timezone.utc),
    )


# Info endpoint
@app.get(
    "/info",
    response_model=InfoResponse,
    status_code=status.HTTP_200_OK,
    tags=["System"],
)
async def get_info():
    return InfoResponse(
        name=os.getenv('API_TITLE', 'DevMesh Platform'),
        version=os.getenv('API_VERSION', '0.1.0'),
        description="AI-Native Observability Platform for Local Infrastructure",
        node=os.getenv('NODE_NAME', 'unknown'),
    )


# Root endpoint
@app.get("/", tags=["System"])
async def root():
    return {
        "message": "DevMesh Platform API",
        "version": os.getenv('API_VERSION', '0.1.0'),
        "endpoints": {
            "health": "/health",
            "info": "/info",
            "docs": "/docs",
            "openapi": "/openapi.json",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv('API_HOST', '0.0.0.0'),
        port=int(os.getenv('API_PORT', 8000)),
        reload=os.getenv('DEV_MODE', 'false').lower() in ('true', '1', 'yes'),  # H5
        log_level="info",
    )
