"""Request correlation ID middleware using ULIDs.

Pure ASGI implementation — no BaseHTTPMiddleware.  Runs in the same asyncio
task as the endpoint so ContextVars propagate correctly in both directions.
"""

from contextvars import ContextVar
from typing import Any

from ulid import ULID

Scope = dict[str, Any]
Receive = Any
Send = Any

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the ULID request ID for the current request, or '' outside a request."""
    return _request_id_var.get()


class RequestIdMiddleware:
    """Generate a ULID per request, store it in a ContextVar, echo in X-Request-ID."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(ULID())
        token = _request_id_var.set(request_id)

        async def send_with_request_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _request_id_var.reset(token)
