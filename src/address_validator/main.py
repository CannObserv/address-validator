"""Address Validator — FastAPI application entry point."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from address_validator.db import engine as db_engine
from address_validator.logging_filter import RequestIdFilter
from address_validator.middleware.api_version import ApiVersionHeaderMiddleware
from address_validator.middleware.audit import AuditMiddleware
from address_validator.middleware.request_id import RequestIdMiddleware
from address_validator.models import ErrorResponse
from address_validator.routers.admin._config import get_css_version
from address_validator.routers.admin._config import templates as admin_templates
from address_validator.routers.admin.deps import AdminAuthRequired, DatabaseUnavailable
from address_validator.routers.admin.router import admin_router
from address_validator.routers.v1 import countries as v1_countries
from address_validator.routers.v1 import health as v1_health
from address_validator.routers.v1 import parse as v1_parse
from address_validator.routers.v1 import standardize as v1_standardize
from address_validator.routers.v1 import validate as v1_validate
from address_validator.routers.v1.core import APIError, api_error_response
from address_validator.services.validation.config import ValidationConfig, validate_config
from address_validator.services.validation.gcp_quota_sync import run_reconciliation_loop
from address_validator.services.validation.registry import ProviderRegistry

_THIS_DIR = Path(__file__).resolve().parent  # src/address_validator/

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logging.getLogger().addFilter(RequestIdFilter())


def _load_custom_model() -> None:
    """Swap usaddress.TAGGER with a custom .crfsuite model if configured.

    Reads CUSTOM_MODEL_PATH from environment. No-op when unset.
    Logs warning and falls back to bundled model if path is invalid.
    """
    import pycrfsuite  # noqa: PLC0415
    import usaddress  # noqa: PLC0415

    custom_path = os.environ.get("CUSTOM_MODEL_PATH", "").strip()
    if not custom_path:
        return

    path = Path(custom_path)
    if not path.exists():
        logging.getLogger(__name__).warning(
            "CUSTOM_MODEL_PATH=%s not found, using bundled model", path
        )
        return

    try:
        tagger = pycrfsuite.Tagger()
        tagger.open(str(path))
        usaddress.TAGGER = tagger
        logging.getLogger(__name__).info("loaded custom usaddress model: %s", path)
    except Exception:
        logging.getLogger(__name__).warning(
            "CUSTOM_MODEL_PATH=%s failed to load, using bundled model", path, exc_info=True
        )


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
    """FastAPI lifespan context — set API key, validate config, and close DB on shutdown."""
    app.state.api_key = os.environ.get("API_KEY", "").strip() or None
    _load_custom_model()

    await db_engine.init_engine()
    try:
        app.state.engine = db_engine.get_engine()  # None when no DSN configured
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
    await db_engine.close_engine()


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


@app.exception_handler(AdminAuthRequired)
async def _admin_auth_redirect(request: Request, exc: AdminAuthRequired) -> Response:
    return RedirectResponse(url=exc.redirect_url, status_code=302)


@app.exception_handler(DatabaseUnavailable)
async def _admin_db_unavailable(request: Request, exc: DatabaseUnavailable) -> Response:
    return admin_templates.TemplateResponse(
        "admin/error_503.html",
        {
            "request": request,
            "user": exc.user,
            "active_nav": "",
            "css_version": get_css_version(),
        },
        status_code=503,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
# ── Middleware ordering is load-bearing ──────────────────────────────
# add_middleware is LIFO: last-registered wraps outermost, so it
# *executes first*.  Execution order (outermost → innermost):
#   ApiVersionHeaderMiddleware → RequestIdMiddleware → AuditMiddleware → CORS
# RequestIdMiddleware must execute BEFORE AuditMiddleware so that
# get_request_id() returns a value when the audit row is written.
# Do NOT reorder these lines.
# Regression tests: tests/unit/test_audit_middleware.py
app.add_middleware(AuditMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(ApiVersionHeaderMiddleware)


@app.exception_handler(APIError)
async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    """Serialise :class:`APIError` directly as the response body.

    Bypasses FastAPI's default ``HTTPException`` wrapping so the wire
    format is ``{"error": "...", "message": "...", "api_version": "1"}``
    rather than ``{"detail": {...}}``.  The ``API-Version`` response
    header is appended by :class:`ApiVersionHeaderMiddleware`.
    """
    return api_error_response(exc)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Convert Pydantic request validation errors to the uniform :class:`ErrorResponse` shape.

    Pydantic raises :exc:`~fastapi.exceptions.RequestValidationError` for
    both field-level failures (e.g. ``max_length``) and model-level
    ``model_validator`` failures.  Without this handler FastAPI emits
    ``{"detail": [...]}``; this handler normalises all 422s to
    ``{"error": "validation_error", "message": "...", "api_version": "1"}``.

    For ``ValueError``-based validators the human message is extracted from
    the exception context (``ctx["error"]``) to avoid the redundant
    ``"Value error, "`` prefix Pydantic v2 prepends to ``msg``.

    The ``API-Version: 1`` response header is appended by
    :class:`ApiVersionHeaderMiddleware` for all ``/api/v1/`` paths.
    """
    messages: list[str] = []
    for err in exc.errors():
        ctx_error = err.get("ctx", {}).get("error")
        messages.append(str(ctx_error) if isinstance(ctx_error, Exception) else err["msg"])
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(
            error="validation_error",
            message="; ".join(messages),
        ).model_dump(),
    )


# v1 routes (current)
app.include_router(v1_health.router)
app.include_router(v1_parse.router)
app.include_router(v1_standardize.router)
app.include_router(v1_validate.router)
app.include_router(v1_countries.router)

# Admin dashboard
app.include_router(admin_router)
app.mount(
    "/static/admin",
    StaticFiles(directory=str(_THIS_DIR / "static" / "admin")),
    name="admin-static",
)
