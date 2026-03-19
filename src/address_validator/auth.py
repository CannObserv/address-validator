"""API-key authentication dependency."""

import logging
import os
import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_MAX_KEY_LENGTH = 256

# Read the key once at import time to avoid re-reading os.environ on every
# request.  None means the env var was absent or empty; require_api_key raises
# 503 on the first authenticated request so the module stays importable by
# test infrastructure, type-checkers, and doc generators without the var set.
_API_KEY: str | None = os.environ.get("API_KEY", "").strip() or None


async def require_api_key(
    request: Request,
    api_key: str | None = Security(_header),
) -> str:
    """Validate the X-API-Key header against the configured key.

    Returns the validated key on success.  Raises 503 when the service is
    misconfigured (API_KEY not set), 401 when the header is missing, and 403
    when the key is invalid.
    """
    path = request.url.path
    if _API_KEY is None:
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
    if not secrets.compare_digest(api_key, _API_KEY):
        logger.info("auth rejected: invalid API key path=%s", path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key
