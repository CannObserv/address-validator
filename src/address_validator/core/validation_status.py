"""Single source of truth for the validation-status vocabulary (GH #136).

Every value that may appear in ``ValidationResult.status`` is enumerated here —
nowhere else. The four downstream consumers derive from or are checked against
this tuple:

- ``models.py`` — the ``ValidationResult.status`` ``Literal`` (a module-level
  assertion pins its members to :data:`VALIDATION_STATUSES`).
- ``db/tables.py`` — the ``validated_addresses.status`` ``CheckConstraint``
  IN-list is built from :data:`VALIDATION_STATUSES`.
- ``services/validation/_helpers.py`` — the DPV→status map values are a subset
  of :data:`VALIDATION_STATUSES`.
- ``routers/admin/_config.py`` — the ``VS_META`` display table keys equal
  :data:`VALIDATION_STATUSES`.

Keep this module and ``docs/VALIDATION-STATUS.md`` in sync — the drift test
``tests/unit/test_validation_status_catalogue.py`` fails on any divergence.
After adding or changing a status, update both — and add a new Alembic
migration widening the ``ck_validated_addresses_status`` CHECK constraint.
The DB constraint is immutable history, so the drift test cannot detect a
missing migration; the live DB will reject an un-migrated status at insert.

This mirrors the response-warning catalogue pattern (``core/warnings.py`` /
``docs/WARNINGS.md``, GH #131/#132).
"""

# --- Status constants ---
#
# Each constant is one machine-readable validation outcome. Semantics are
# documented alongside ``ValidationResult.status`` in ``models.py`` and in
# ``docs/VALIDATION-STATUS.md``.

CONFIRMED = "confirmed"
CONFIRMED_MISSING_SECONDARY = "confirmed_missing_secondary"
CONFIRMED_BAD_SECONDARY = "confirmed_bad_secondary"
NOT_CONFIRMED = "not_confirmed"
NOT_FOUND = "not_found"
INVALID = "invalid"
UNAVAILABLE = "unavailable"
ERROR = "error"

# Authoritative list of every validation status. The drift test checks this
# against the Literal, the DB constraint, the DPV map, VS_META, and
# docs/VALIDATION-STATUS.md, so a new status is only "live" once it appears
# here.
VALIDATION_STATUSES: tuple[str, ...] = (
    CONFIRMED,
    CONFIRMED_MISSING_SECONDARY,
    CONFIRMED_BAD_SECONDARY,
    NOT_CONFIRMED,
    NOT_FOUND,
    INVALID,
    UNAVAILABLE,
    ERROR,
)
