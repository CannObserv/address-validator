"""Audit logging middleware — records every API request to the audit_log table.

Pure ASGI implementation — no BaseHTTPMiddleware.  Runs in the same asyncio
task as the endpoint so ContextVars set by the validation pipeline (provider,
status, cache_hit) are visible when the audit row is queued.

Rows are written through a bounded queue serviced by a single writer task
(:class:`AuditWriteQueue`, GH #180): bursts beyond DB throughput apply
backpressure by dropping rows (fail-open, logged) instead of accumulating
unbounded fire-and-forget tasks, and the queue is drained on lifespan
shutdown so final in-flight rows are not lost on restart.

Skips non-API routes: /, /docs, /redoc, /openapi.json, /admin/*, /static/*.
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from address_validator.middleware.request_id import get_request_id
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_parse_type,
    get_audit_pattern_key,
    get_audit_provider,
    get_audit_raw_input,
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

# Signals the writer task to exit after the queue ahead of it is drained.
_SENTINEL: Any = object()

_QueueItemKind = Literal["audit", "candidate"]


class AuditWriteQueue:
    """Bounded queue + single writer task for audit and candidate rows (GH #180).

    ``enqueue()`` is synchronous and never blocks the request path: when the
    queue is full the row is dropped with a WARNING (fail-open — the audit
    trail is advisory, like the writes themselves).  The writer task starts
    lazily on first enqueue (so apps without a lifespan, e.g. test minis,
    still write) and resolves ``write_audit_row`` / ``write_training_candidate``
    from this module's globals at call time, preserving the established
    patch points.  ``aclose()`` drains everything already queued, then stops
    the writer — wired to lifespan shutdown by :class:`AuditMiddleware`.
    """

    def __init__(self, maxsize: int = 1000, close_timeout_s: float = 5.0) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._close_timeout_s = close_timeout_s
        self._pending = 0

    @property
    def pending(self) -> int:
        """Rows enqueued but not yet written (queued + in-flight)."""
        return self._pending

    def enqueue(self, kind: _QueueItemKind, engine: Any, kwargs: dict[str, Any]) -> None:
        """Queue one row for the writer; drop it (logged) when the queue is full."""
        self._ensure_started()
        try:
            self._queue.put_nowait((kind, engine, kwargs))
            self._pending += 1
        except asyncio.QueueFull:
            logger.warning(
                "audit_queue: full (%d pending) — dropping %s row (fail-open)",
                self._queue.qsize(),
                kind,
            )

    def _ensure_started(self) -> None:
        """Start (or restart) the writer task on the running loop.

        The loop identity check matters under test: each test gets a fresh
        event loop while the app (and this queue) is module-scoped, so a
        writer started on an earlier, now-closed loop must be replaced.
        In production there is a single loop and this never re-fires.
        """
        loop = asyncio.get_running_loop()
        if self._task is None or self._task.done() or self._loop is not loop:
            if self._loop is not loop:
                # A writer abandoned mid-write on a dead loop never ran its
                # finally block — resynchronize the counter with what is
                # actually still queued so `pending` cannot drift upward.
                self._pending = self._queue.qsize()
            self._loop = loop
            self._task = loop.create_task(self._run(), name="audit-write-queue")

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                kind, engine, kwargs = item
                if kind == "audit":
                    await write_audit_row(engine, **kwargs)
                else:
                    await write_training_candidate(engine=engine, **kwargs)
            except Exception:
                # write_* are fail-open already; this is defence in depth so a
                # bug in the writer itself can never kill the queue consumer.
                logger.exception("audit_queue: writer failed for a queued row")
            finally:
                if item is not _SENTINEL:
                    self._pending -= 1
                self._queue.task_done()

    async def aclose(self) -> None:
        """Drain queued rows and stop the writer (bounded by close_timeout_s).

        No-op when the writer belongs to a different event loop (nested test
        apps sharing this queue): awaiting a foreign-loop task raises, and
        that loop's own lifespan shutdown is responsible for the drain.
        """
        task = self._task
        if task is None or task.done():
            self._task = None
            return
        if self._loop is not asyncio.get_running_loop():
            return
        try:
            async with asyncio.timeout(self._close_timeout_s):
                await self._queue.put(_SENTINEL)
                await task
        except TimeoutError:
            logger.warning(
                "audit_queue: drain timed out — cancelling writer with %d rows pending",
                self._queue.qsize(),
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None


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
    queue: AuditWriteQueue,
) -> None:
    """Queue audit + candidate rows on the bounded write queue.

    Called from both the success and exception paths in AuditMiddleware. No-op
    when the app has no engine configured. `exc_info` accepts `BaseException`
    so that `asyncio.CancelledError` (and other non-`Exception` shutdown
    conditions) still produce a labeled audit row instead of a `status_code=0`
    row with no `error_detail`.  Enqueueing is synchronous — safe to call from
    the middleware's ``finally`` block even under cancellation.
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
    raw_input = get_audit_raw_input()

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

    queue.enqueue(
        "audit",
        engine,
        {
            "timestamp": datetime.now(UTC),
            "request_id": get_request_id() or None,
            "client_ip": _get_client_ip(scope),
            "method": scope.get("method", ""),
            "endpoint": path,
            "status_code": status_code,
            "latency_ms": elapsed_ms,
            "provider": provider,
            "validation_status": validation_status,
            "cache_hit": cache_hit,
            "error_detail": error_detail,
            "pattern_key": pattern_key,
            "parse_type": parse_type,
            "raw_input": raw_input,
        },
    )

    # Skip training candidate write on exception — parser may have left
    # partial ContextVar state we don't want to persist.
    if exc_info is not None:
        return
    candidate = get_candidate_data()
    if candidate is None:
        return
    api_version: str | None = "2" if path.startswith("/api/v2/") else None
    queue.enqueue(
        "candidate",
        engine,
        {
            "endpoint": path,
            "provider": provider,
            "api_version": api_version,
            **candidate,
        },
    )


class AuditMiddleware:
    """Record API requests to the audit_log table after the response is sent.

    Owns the :class:`AuditWriteQueue`.  On the ASGI ``lifespan`` scope the
    middleware intercepts the ``lifespan.shutdown`` message and drains the
    queue *before* forwarding it inward, so all queued rows are written while
    the app's engine is still open (the lifespan handler closes it later).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.queue = AuditWriteQueue()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, self._draining_receive(receive), send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Publish the queue for observability (tests poll `pending` to wait
        # for writes deterministically; cheap identity check per request).
        app = scope.get("app")
        if app is not None and getattr(app.state, "audit_queue", None) is not self.queue:
            app.state.audit_queue = self.queue

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
                _emit_audit_artifacts(scope, path, status_code, elapsed_ms, exc_info, self.queue)
            except Exception:
                # Defense in depth: a bug in the helper must never mask the
                # exception we are re-raising (or suppress a clean return).
                logger.exception(
                    "audit: post-request write block failed for %s (request_id=%s)",
                    path,
                    get_request_id() or "?",
                )

    def _draining_receive(self, receive: Receive) -> Receive:
        """Wrap *receive* so the write queue drains on lifespan shutdown."""

        async def wrapped() -> Message:
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                await self.queue.aclose()
            return message

        return wrapped
