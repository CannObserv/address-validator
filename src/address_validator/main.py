"""Address Validator — FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from address_validator.logging_filter import RequestIdFilter
from address_validator.middleware.request_id import request_id_middleware
from address_validator.routers.v1 import health as v1_health
from address_validator.routers.v1 import parse as v1_parse
from address_validator.routers.v1 import standardize as v1_standardize
from address_validator.routers.v1 import validate as v1_validate
from address_validator.routers.v1.core import APIError, api_error_response
from address_validator.services.validation.cache_db import close_db
from address_validator.services.validation.factory import validate_config

logging.getLogger().addFilter(RequestIdFilter())

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context — validate config on startup, close DB on shutdown."""
    validate_config()
    yield
    await close_db()


app = FastAPI(
    lifespan=lifespan,
    title="Address Validator API",
    description=_DESCRIPTION,
    # Service version (semver). Bumped to 2.0.0 when unversioned /api/* routes
    # were removed (issue #12). Note: this is distinct from the api_version
    # field in response bodies, which tracks the /api/v1/ route namespace and
    # will not change when the service version increments.
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
app.middleware("http")(request_id_middleware)


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
app.include_router(v1_validate.router)
