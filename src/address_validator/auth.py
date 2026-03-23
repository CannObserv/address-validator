"""API-key authentication dependency."""

import logging
import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_MAX_KEY_LENGTH = 256


async def require_api_key(
    request: Request,
    api_key: str | None = Security(_header),
) -> str:
    """Validate the X-API-Key header against the configured key.

    The configured key is read from ``request.app.state.api_key``, which is
    set by the lifespan startup hook in ``main.py``.  This keeps auth.py free
    of import-time side-effects and eliminates the fragile import-ordering
    constraint that previously existed in conftest.py.

    Returns the validated key on success.  Raises 503 when the service is
    misconfigured (API_KEY not set), 401 when the header is missing, and 403
    when the key is invalid.
    """
    configured_key: str | None = getattr(request.app.state, "api_key", None)
    path = request.url.path
    if configured_key is None:
        logger.error("auth: API_KEY not configured, rejecting request path=%s", path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service misconfigured: API key not set.",
        )
    if api_key is None:
        logger.info("auth rejected: missing API key path=%s", path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide an X-API-Key header.",
        )
    if len(api_key) > _MAX_KEY_LENGTH:
        logger.info("auth rejected: invalid API key path=%s", path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    if not secrets.compare_digest(api_key, configured_key):
        logger.info("auth rejected: invalid API key path=%s", path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key
