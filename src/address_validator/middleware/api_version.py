"""API version header middleware.

Pure ASGI implementation — appends ``API-Version: 2`` to all responses
on ``/api/v2/`` routes.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ApiVersionHeaderMiddleware:
    """Append ``API-Version: 2`` to responses on ``/api/v2/`` routes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not path.startswith("/api/v2/"):
            await self.app(scope, receive, send)
            return

        async def send_with_api_version(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"api-version", b"2"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_api_version)
