"""API-key authentication dependency."""

import os
import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_MAX_KEY_LENGTH = 256

# Read the key once at import time so the service fails fast if it is
# missing and avoids re-reading os.environ on every request.
_API_KEY: str = os.environ.get("API_KEY", "").strip()
if not _API_KEY:
    raise RuntimeError(
        "API_KEY environment variable is not set or empty. "
        "The service cannot start without a configured API key."
    )


async def require_api_key(
    api_key: str | None = Security(_header),
) -> str:
    """Validate the X-API-Key header against the configured key.

    Returns the validated key on success.  Raises 401 when the header
    is missing and 403 when the key is invalid.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide an X-API-Key header.",
        )
    if len(api_key) > _MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    if not secrets.compare_digest(api_key, _API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key
