"""API-key authentication dependency."""

import os
import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_api_key() -> str:
    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError(
            "API_KEY environment variable is not set. "
            "The service cannot authenticate requests."
        )
    return key


async def require_api_key(
    api_key: str | None = Security(_header),
) -> str:
    """Validate the X-API-Key header against the configured key.

    Returns the validated key on success; raises 401 on failure.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide an X-API-Key header.",
        )
    expected = _get_api_key()
    if not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key
