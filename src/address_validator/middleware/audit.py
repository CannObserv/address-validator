"""Audit logging middleware — records every API request to the audit_log table.

Pure ASGI implementation — no BaseHTTPMiddleware.  Runs in the same asyncio
task as the endpoint so ContextVars set by the validation pipeline (provider,
status, cache_hit) are visible when the audit row is written.

Skips non-API routes: /, /docs, /redoc, /openapi.json, /admin/*, /static/*.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from address_validator.middleware.request_id import get_request_id
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_parse_type,
    get_audit_pattern_key,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    write_audit_row,
)
from address_validator.services.training_candidates import (
    get_candidate_data,
    reset_candidate_data,
    write_training_candidate,
)

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


def _get_client_ip(scope: Scope) -> str:
    """Extract client IP from X-Forwarded-For or fall back to scope client."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode().split(",")[0].strip()
    client = scope.get("client")
    if client:
        return client[0]
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


_VALIDATE_ENDPOINTS = frozenset({"/api/v2/validate"})
_2XX_MIN = 200
_2XX_MAX = 300


def _check_validate_invariants(
    endpoint: str,
    status_code: int,
    provider: str | None,
    validation_status: str | None,
    cache_hit: bool | None,
) -> bool:
    """Check that a successful /validate audit row has all expected fields.

    Returns True when invariants hold, False when violated (and logs WARNING).
    Applies to /api/v2/validate with 2xx status codes.
    """
    if endpoint not in _VALIDATE_ENDPOINTS:
        return True
    if not (_2XX_MIN <= status_code < _2XX_MAX):
        return True

    missing = []
    if provider is None:
        missing.append("provider")
    if validation_status is None:
        missing.append("validation_status")
    if cache_hit is None:
        missing.append("cache_hit")

    if missing:
        logger.warning(
            "audit_invariant_violated: %s 2xx but NULL fields: %s",
            endpoint,
            ", ".join(missing),
        )
        return False

    return True


def _emit_audit_artifacts(
    scope: Scope,
    path: str,
    status_code: int,
    elapsed_ms: int,
    exc_info: BaseException | None,
) -> None:
    """Queue fire-and-forget audit + candidate writes.

    Called from both the success and exception paths in AuditMiddleware. No-op
    when the app has no engine configured. `exc_info` accepts `BaseException`
    so that `asyncio.CancelledError` (and other non-`Exception` shutdown
    conditions) still produce a labeled audit row instead of a `status_code=0`
    row with no `error_detail`.
    """
    app = scope.get("app")
    engine = getattr(app.state, "engine", None) if app else None
    if engine is None:
        return

    provider = get_audit_provider()
    validation_status = get_audit_validation_status()
    cache_hit = get_audit_cache_hit()
    pattern_key = get_audit_pattern_key()
    parse_type = get_audit_parse_type()

    error_detail: str | None
    if exc_info is not None:
        # Preserve a partial status when http.response.start fired before the
        # raise — that's what the client actually received (likely a truncated
        # 2xx body). Fall back to 500 only when no status header fired;
        # ServerErrorMiddleware will then synthesize a 500 for the client.
        if status_code == 0:
            status_code = 500
        error_detail = f"unhandled_exception:{type(exc_info).__name__}"
    else:
        error_detail = _error_detail_from_status(status_code)
        if not _check_validate_invariants(
            path, status_code, provider, validation_status, cache_hit
        ):
            error_detail = "audit_invariant_violated"

    task = asyncio.create_task(
        write_audit_row(
            engine,
            timestamp=datetime.now(UTC),
            request_id=get_request_id() or None,
            client_ip=_get_client_ip(scope),
            method=scope.get("method", ""),
            endpoint=path,
            status_code=status_code,
            latency_ms=elapsed_ms,
            provider=provider,
            validation_status=validation_status,
            cache_hit=cache_hit,
            error_detail=error_detail,
            pattern_key=pattern_key,
            parse_type=parse_type,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # Skip training candidate write on exception — parser may have left
    # partial ContextVar state we don't want to persist.
    if exc_info is not None:
        return
    candidate = get_candidate_data()
    if candidate is None:
        return
    api_version: str | None = "2" if path.startswith("/api/v2/") else None
    candidate_task = asyncio.create_task(
        write_training_candidate(
            engine=engine,
            endpoint=path,
            provider=provider,
            api_version=api_version,
            **candidate,
        )
    )
    _background_tasks.add(candidate_task)
    candidate_task.add_done_callback(_background_tasks.discard)


class AuditMiddleware:
    """Record API requests to the audit_log table after the response is sent."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not _should_audit(path):
            await self.app(scope, receive, send)
            return

        reset_audit_context()
        reset_candidate_data()

        status_code = 0
        start = time.monotonic()
        exc_info: BaseException | None = None

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        except BaseException as exc:
            # ServerErrorMiddleware (outside this middleware) still needs to see
            # the exception so it can synthesize a 500 — re-raise after queuing
            # the audit-row write in finally. `BaseException` is intentional:
            # `asyncio.CancelledError` is BaseException in 3.8+, and a cancelled
            # request without this catch produces a status_code=0 row with no
            # error_detail.
            exc_info = exc
            raise
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            try:
                _emit_audit_artifacts(scope, path, status_code, elapsed_ms, exc_info)
            except Exception:
                # Defense in depth: a bug in the helper must never mask the
                # exception we are re-raising (or suppress a clean return).
                logger.exception(
                    "audit: post-request write block failed for %s (request_id=%s)",
                    path,
                    get_request_id() or "?",
                )
