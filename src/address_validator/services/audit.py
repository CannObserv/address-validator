"""Audit logging — ContextVars for passing validation metadata to middleware.

The audit middleware (middleware/audit.py) reads these ContextVars after the
request completes to enrich audit_log rows with validation-specific fields.
The cache provider sets them during validate() so the middleware doesn't need
to understand the validation pipeline.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from address_validator.db.tables import audit_log

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_audit_provider: ContextVar[str | None] = ContextVar("audit_provider", default=None)
_audit_validation_status: ContextVar[str | None] = ContextVar(
    "audit_validation_status", default=None
)
_audit_cache_hit: ContextVar[bool | None] = ContextVar("audit_cache_hit", default=None)
_audit_pattern_key: ContextVar[str | None] = ContextVar("audit_pattern_key", default=None)
_audit_parse_type: ContextVar[str | None] = ContextVar("audit_parse_type", default=None)


def get_audit_provider() -> str | None:
    return _audit_provider.get()


def get_audit_validation_status() -> str | None:
    return _audit_validation_status.get()


def get_audit_cache_hit() -> bool | None:
    return _audit_cache_hit.get()


def get_audit_pattern_key() -> str | None:
    return _audit_pattern_key.get()


def get_audit_parse_type() -> str | None:
    return _audit_parse_type.get()


def reset_audit_context() -> None:
    """Reset all audit ContextVars to their defaults (None).

    Called at the start of each audited request to prevent stale values
    from a previous request leaking through on the same asyncio task.
    """
    _audit_provider.set(None)
    _audit_validation_status.set(None)
    _audit_cache_hit.set(None)
    _audit_pattern_key.set(None)
    _audit_parse_type.set(None)


def set_audit_context(
    *,
    provider: str | None = None,
    validation_status: str | None = None,
    cache_hit: bool | None = None,
    pattern_key: str | None = None,
    parse_type: str | None = None,
) -> None:
    """Set audit ContextVars for the current request."""
    if provider is not None:
        _audit_provider.set(provider)
    if validation_status is not None:
        _audit_validation_status.set(validation_status)
    if cache_hit is not None:
        _audit_cache_hit.set(cache_hit)
    if pattern_key is not None:
        _audit_pattern_key.set(pattern_key)
    if parse_type is not None:
        _audit_parse_type.set(parse_type)


async def write_audit_row(
    engine: AsyncEngine,
    *,
    timestamp: datetime,
    request_id: str | None,
    client_ip: str,
    method: str,
    endpoint: str,
    status_code: int,
    latency_ms: int | None,
    provider: str | None,
    validation_status: str | None,
    cache_hit: bool | None,
    error_detail: str | None,
    pattern_key: str | None = None,
    parse_type: str | None = None,
) -> None:
    """Insert a single audit_log row. Logs and swallows all errors (fail-open)."""
    try:
        async with engine.begin() as conn:
            await conn.execute(
                audit_log.insert().values(
                    timestamp=timestamp,
                    request_id=request_id,
                    client_ip=client_ip,
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    provider=provider,
                    validation_status=validation_status,
                    cache_hit=cache_hit,
                    error_detail=error_detail,
                    pattern_key=pattern_key,
                    parse_type=parse_type,
                )
            )
    except Exception:
        logger.warning("audit: failed to write audit row", exc_info=True)
