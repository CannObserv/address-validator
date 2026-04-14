"""Training candidate collection — ContextVars + fire-and-forget DB insert.

When the parser encounters a RepeatedLabelError or triggers post-parse recovery
heuristics, this module records the raw address and token data as a training
candidate for future CRF model improvements.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from address_validator.db.tables import model_training_candidates

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_candidate_data: ContextVar[dict[str, Any] | None] = ContextVar(
    "training_candidate_data", default=None
)


def set_candidate_data(
    *,
    raw_address: str,
    failure_type: str,
    parsed_tokens: list[tuple[str, str]] | list[Any],
    recovered_components: dict[str, str] | None = None,
    failure_reason: str | None = None,
) -> None:
    """Set training candidate data for the current request context."""
    _candidate_data.set(
        {
            "raw_address": raw_address,
            "failure_type": failure_type,
            "parsed_tokens": parsed_tokens,
            "recovered_components": recovered_components,
            "failure_reason": failure_reason,
        }
    )


def get_candidate_data() -> dict[str, Any] | None:
    """Read training candidate data for the current request context."""
    return _candidate_data.get()


def reset_candidate_data() -> None:
    """Reset candidate ContextVar to None."""
    _candidate_data.set(None)


async def write_training_candidate(
    engine: AsyncEngine | None,
    *,
    raw_address: str,
    failure_type: str,
    parsed_tokens: list[tuple[str, str]] | list[Any],
    recovered_components: dict[str, str] | None = None,
    endpoint: str | None = None,
    provider: str | None = None,
    api_version: str | None = None,
    failure_reason: str | None = None,
) -> None:
    """Insert a training candidate row. Logs and swallows all errors (fail-open)."""
    if engine is None:
        return
    try:
        tokens_json = [[tok, label] for tok, label in parsed_tokens]
        async with engine.begin() as conn:
            await conn.execute(
                model_training_candidates.insert().values(
                    raw_address=raw_address,
                    failure_type=failure_type,
                    parsed_tokens=tokens_json,
                    recovered_components=recovered_components,
                    endpoint=endpoint,
                    provider=provider,
                    api_version=api_version,
                    failure_reason=failure_reason,
                )
            )
    except Exception:
        logger.warning("training_candidates: failed to write training candidate", exc_info=True)
