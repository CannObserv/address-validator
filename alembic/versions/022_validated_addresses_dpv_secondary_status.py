"""Relabel cached DPV D/S rows with the corrected secondary statuses.

Revision ID: 022
Revises: 021
Create Date: 2026-10-01

The DPV→status map swapped D and S against the USPS spec (GH #253): D (secondary
missing) was stored as ``confirmed_bad_secondary`` and S (secondary present but
not confirmed) as ``confirmed_missing_secondary``. Cache hits serve the stored
status verbatim, so the map fix alone leaves every cached D/S answer wrong.

The status is derived from ``dpv_match_code`` rather than swapped, so a row the
fixed code already wrote stays correct and re-running the statement is a no-op.
``audit_log.validation_status`` is deliberately not rewritten: it records what
clients were served, and rows past the retention window already live in the
GCS Parquet archive (cutover documented in docs/VALIDATION-STATUS.md).
"""

revision: str = "022"
down_revision: str = "021"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402

_UPGRADE = (
    "UPDATE validated_addresses SET status = CASE dpv_match_code"
    " WHEN 'D' THEN 'confirmed_missing_secondary'"
    " WHEN 'S' THEN 'confirmed_bad_secondary' END"
    " WHERE dpv_match_code IN ('D', 'S')"
)
# Restores the pre-#253 labels the reverted code writes and expects.
_DOWNGRADE = (
    "UPDATE validated_addresses SET status = CASE dpv_match_code"
    " WHEN 'D' THEN 'confirmed_bad_secondary'"
    " WHEN 'S' THEN 'confirmed_missing_secondary' END"
    " WHERE dpv_match_code IN ('D', 'S')"
)


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
