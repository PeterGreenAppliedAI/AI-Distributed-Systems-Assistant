"""
DevMesh Platform - API Key Authentication Middleware (B1)

Checks X-API-Key header on protected endpoints.
Gated by API_AUTH_ENABLED env var for safe rollout.
"""

import os
import hmac
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that never require authentication
PUBLIC_PATHS = {"/health", "/info", "/", "/docs", "/openapi.json", "/redoc", "/favicon.ico", "/ui"}

# Path prefixes that never require authentication. Static UI assets contain
# no data; every data call the UI makes (/analyze) still requires X-API-Key.
PUBLIC_PREFIXES = ("/static/",)


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Check if auth is enabled
        if not os.getenv("API_AUTH_ENABLED", "false").lower() in ("true", "1", "yes"):
            return await call_next(request)

        # Skip auth for public paths
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        # Get expected key
        expected_key = os.getenv("API_KEY")
        if not expected_key:
            logger.warning("API_AUTH_ENABLED=true but API_KEY not set; rejecting all protected requests")
            return JSONResponse(
                status_code=500,
                content={"error_code": "AUTH_NOT_CONFIGURED", "message": "Server authentication not configured"},
            )

        # Validate header
        provided_key = request.headers.get("X-API-Key")
        if not provided_key or not hmac.compare_digest(provided_key.encode(), expected_key.encode()):
            return JSONResponse(
                status_code=401,
                content={"error_code": "UNAUTHORIZED", "message": "Invalid or missing API key"},
            )

        return await call_next(request)
