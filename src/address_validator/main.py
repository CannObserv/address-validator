"""Address Validator — FastAPI application entry point."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from address_validator.logging_filter import RequestIdFilter
from address_validator.middleware.audit import audit_middleware
from address_validator.middleware.request_id import request_id_middleware
from address_validator.routers.admin.router import admin_router
from address_validator.routers.v1 import health as v1_health
from address_validator.routers.v1 import parse as v1_parse
from address_validator.routers.v1 import standardize as v1_standardize
from address_validator.routers.v1 import validate as v1_validate
from address_validator.routers.v1.core import APIError, api_error_response
from address_validator.services.validation.cache_db import close_engine, get_engine, init_engine
from address_validator.services.validation.config import ValidationConfig, validate_config
from address_validator.services.validation.gcp_quota_sync import run_reconciliation_loop
from address_validator.services.validation.registry import ProviderRegistry

_THIS_DIR = Path(__file__).resolve().parent  # src/address_validator/

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
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
    await init_engine()
    try:
        app.state.engine = get_engine()
    except RuntimeError:
        app.state.engine = None

    config = validate_config()
    if config is None:
        config = ValidationConfig()
    registry = ProviderRegistry(config)

    # Eagerly construct provider singletons so quota sync wiring runs at boot
    registry.get_provider()
    app.state.registry = registry

    # Start reconciliation background task if Google provider is active
    reconciliation_task = None
    params = registry.get_reconciliation_params()
    if params:
        reconciliation_task = asyncio.create_task(
            run_reconciliation_loop(**params),
            name="google-quota-reconciliation",
        )

    yield

    # Cancel reconciliation task on shutdown
    if reconciliation_task is not None:
        reconciliation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconciliation_task

    await registry.close()
    await close_engine()


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
# ── Middleware ordering is load-bearing ──────────────────────────────
# FastAPI middleware is LIFO: last-registered wraps outermost, so it
# *executes first*.  request_id_middleware must execute BEFORE
# audit_middleware so that get_request_id() returns a value when the
# audit row is written.  Do NOT reorder these two lines.
# Regression test: tests/unit/test_audit_middleware.py::test_audit_row_receives_request_id
app.middleware("http")(audit_middleware)
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

# Admin dashboard
app.include_router(admin_router)
app.mount(
    "/static/admin",
    StaticFiles(directory=str(_THIS_DIR / "static" / "admin")),
    name="admin-static",
)
