"""Structured API error types shared across v1 and v2 routers."""

from fastapi.responses import JSONResponse

from address_validator.models import ErrorResponse


class APIError(Exception):
    """Raised by route handlers to produce a structured ErrorResponse body.

    Caught by the ``api_error_handler`` registered in ``main.py``, which
    serialises it directly as the response body (no ``{"detail": ...}``
    wrapping).  The ``API-Version`` header is added by middleware.
    """

    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        self.headers = headers


def api_error_response(exc: "APIError") -> JSONResponse:
    """Serialise *exc* to a :class:`JSONResponse` with the correct status code.

    Called from the exception handler registered in ``main.py``.
    Uses :class:`~address_validator.models.ErrorResponse` directly to ensure
    the wire format stays in sync with the model schema.  ``models`` imports
    nothing from ``routers`` or ``core``, so there is no circular dependency.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.error,
            message=exc.message,
        ).model_dump(),
        headers=exc.headers,
    )
