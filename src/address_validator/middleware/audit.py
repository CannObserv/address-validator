"""Audit logging middleware — records every API request to the audit_log table.

Runs inside the request_id middleware so the ULID is available via ContextVar.
Captures timing, client IP, status code, and validation-specific ContextVars
(provider, status, cache_hit). Writes are fire-and-forget via asyncio.create_task
to avoid adding latency to the response path.

Skips non-API routes: /, /docs, /redoc, /openapi.json, /admin/*, /static/*.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import Request, Response

from address_validator.middleware.request_id import get_request_id
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    write_audit_row,
)
from address_validator.services.validation.cache_db import get_engine

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task[None]] = set()

_SKIP_PREFIXES = ("/admin", "/static", "/docs", "/redoc")
_SKIP_EXACT = {"/", "/openapi.json"}


def _should_audit(path: str) -> bool:
    """Return True if the request path should be recorded in the audit log."""
    if path in _SKIP_EXACT:
        return False
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For or fall back to request.client."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _error_detail_from_status(status_code: int) -> str | None:
    """Return a short error label for 4xx/5xx responses."""
    _client_error_threshold = 400
    if status_code < _client_error_threshold:
        return None
    phrases = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        503: "service_unavailable",
    }
    return phrases.get(status_code, f"http_{status_code}")


async def audit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Record API requests to the audit_log table after the response is sent."""
    path = request.url.path

    if not _should_audit(path):
        return await call_next(request)

    # Reset audit ContextVars so non-validate requests don't inherit
    # stale values from a previous request on the same asyncio task.
    reset_audit_context()

    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Fire-and-forget: write audit row without blocking the response
    try:
        engine = get_engine()
    except Exception:
        return response

    task = asyncio.create_task(
        write_audit_row(
            engine,
            timestamp=datetime.now(UTC),
            request_id=get_request_id() or None,
            client_ip=_get_client_ip(request),
            method=request.method,
            endpoint=path,
            status_code=response.status_code,
            latency_ms=elapsed_ms,
            provider=get_audit_provider(),
            validation_status=get_audit_validation_status(),
            cache_hit=get_audit_cache_hit(),
            error_detail=_error_detail_from_status(response.status_code),
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return response
