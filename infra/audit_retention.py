"""The audit_log retention window, shared by the archiver and its age check (#249).

Stdlib only: ``archive_age_check.py`` imports this without pulling in the
archiver's pyarrow and GCS dependencies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_RETENTION_DAYS = 90


def retention_days(env: Mapping[str, str]) -> int:
    """``AUDIT_RETENTION_DAYS`` as an int; raises ValueError naming the bad value."""
    raw = env.get("AUDIT_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"AUDIT_RETENTION_DAYS must be an integer, got {raw!r}") from None


def compute_cutoff(now: datetime, retention_days: int) -> datetime:
    """Return the UTC midnight ``retention_days`` before ``now``.

    Rows strictly before it are expired. Flooring to midnight keeps each
    archived day whole within one run (#228).
    """
    return (now.astimezone(UTC) - timedelta(days=retention_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
