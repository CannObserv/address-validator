"""Request correlation ID middleware using ULIDs."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from ulid import ULID

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the ULID request ID for the current request, or '' outside a request."""
    return _request_id_var.get()


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Generate a ULID per request, store it in a ContextVar, echo in X-Request-ID."""
    request_id = str(ULID())
    token = _request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        _request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response
