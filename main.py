"""Address Validator — FastAPI application entry point."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers.v1 import health as v1_health
from routers.v1 import parse as v1_parse
from routers.v1 import standardize as v1_standardize
from routers.v1.core import APIError, api_error_response

_DESCRIPTION = """
Parse and standardize physical addresses.

Uses geography-neutral field names (`region`, `postal_code`) and is
designed to support multiple countries as additional parsers are added.
Currently **US addresses only** (USPS Publication 28).

## Versioning

| Prefix | Status |
|---|---|
| `/api/v1/` | **Current** |
"""

_TAGS = [
    {
        "name": "v1",
        "description": "Current API — versioned routes under `/api/v1/`.",
    },
    {
        "name": "health",
        "description": "Service liveness probe. No authentication required.",
    },
]

app = FastAPI(
    title="Address Validator API",
    description=_DESCRIPTION,
    version="2.0.0",
    openapi_tags=_TAGS,
    contact={"name": "Cannabis Observer", "email": "greg@cannabis.observer"},
    license_info={"name": "Proprietary"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(APIError)
async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    """Serialise :class:`APIError` directly as the response body.

    Bypasses FastAPI's default ``HTTPException`` wrapping so the wire
    format is ``{"error": "...", "message": "...", "api_version": "1"}``
    rather than ``{"detail": {...}}``.  The ``API-Version`` response
    header is appended downstream by :func:`add_api_version_header`.
    """
    return api_error_response(exc)


@app.middleware("http")
async def add_api_version_header(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Append ``API-Version: 1`` to all responses on ``/api/v1/`` routes."""
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        response.headers["API-Version"] = "1"
    return response


# v1 routes (current)
app.include_router(v1_health.router)
app.include_router(v1_parse.router)
app.include_router(v1_standardize.router)
