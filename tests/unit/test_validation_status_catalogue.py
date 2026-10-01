"""Drift guard for the validation-status vocabulary (GH #136).

Asserts that the single source of truth
(:data:`address_validator.core.validation_status.VALIDATION_STATUSES`) stays in
sync with every downstream consumer:

- the ``ValidationResult.status`` ``Literal`` in ``models.py``,
- the ``validated_addresses.status`` ``CheckConstraint`` in ``db/tables.py``,
- the DPV→status map in ``services/validation/_helpers.py`` (subset),
- the ``VS_META`` display table in ``routers/admin/_config.py``,
- the consumer-facing catalogue ``docs/VALIDATION-STATUS.md``, and
- the DPV code columns of ``docs/VALIDATION-STATUS.md`` and
  ``docs/VALIDATION-PROVIDERS.md`` (must equal the DPV→status map, GH #253).

If you add or change a validation status, update both
``core/validation_status.py`` and ``docs/VALIDATION-STATUS.md`` — this test
fails on any divergence.
"""

import re
from pathlib import Path
from typing import get_args

from address_validator.core.validation_status import VALIDATION_STATUSES
from address_validator.db.tables import validated_addresses
from address_validator.models import ValidationResult
from address_validator.routers.admin._config import VS_META
from address_validator.services.validation._helpers import _DPV_TO_STATUS

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_DOC = REPO_ROOT / "docs" / "VALIDATION-STATUS.md"
PROVIDERS_DOC = REPO_ROOT / "docs" / "VALIDATION-PROVIDERS.md"


def _documented_statuses() -> set[str]:
    """Extract statuses from the backtick-quoted first column of the catalogue
    table in ``docs/VALIDATION-STATUS.md``."""
    text = STATUS_DOC.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^\|\s*`([^`]+)`\s*\|", text, flags=re.MULTILINE)}


def _status_doc_dpv_map() -> dict[str, str]:
    """DPV code → status from the catalogue table rows that carry a DPV code
    (``| `status` | D | ... |``) in ``docs/VALIDATION-STATUS.md``."""
    text = STATUS_DOC.read_text(encoding="utf-8")
    rows = re.finditer(r"^\|\s*`([a-z_]+)`\s*\|\s*([A-Z])\s*\|", text, flags=re.MULTILINE)
    return {m.group(2): m.group(1) for m in rows}


def _providers_doc_dpv_map() -> dict[str, str]:
    """DPV code → status from the "DPV status mapping" table
    (``| `D` | `status` | ... |``) in ``docs/VALIDATION-PROVIDERS.md``."""
    text = PROVIDERS_DOC.read_text(encoding="utf-8")
    rows = re.finditer(r"^\|\s*`([A-Z])`\s*\|\s*`([a-z_]+)`\s*\|", text, flags=re.MULTILINE)
    return {m.group(1): m.group(2) for m in rows}


def _check_constraint_statuses() -> set[str]:
    """Pull the IN-list literals from the ``validated_addresses.status``
    ``CheckConstraint`` SQL text."""
    sqltexts = [
        str(c.sqltext)
        for c in validated_addresses.c.status.constraints
        if c.name == "ck_validated_addresses_status"
    ]
    assert sqltexts, "ck_validated_addresses_status constraint not found"
    return set(re.findall(r"'([^']+)'", sqltexts[0]))


def test_doc_exists() -> None:
    assert STATUS_DOC.is_file(), f"missing catalogue doc: {STATUS_DOC}"


def test_catalogue_is_nonempty() -> None:
    assert VALIDATION_STATUSES, "VALIDATION_STATUSES must list every validation status"


def test_literal_matches_catalogue() -> None:
    literal = get_args(ValidationResult.model_fields["status"].annotation)
    assert set(literal) == set(VALIDATION_STATUSES), (
        "ValidationResult.status Literal diverges from VALIDATION_STATUSES"
    )


def test_check_constraint_matches_catalogue() -> None:
    assert _check_constraint_statuses() == set(VALIDATION_STATUSES), (
        "validated_addresses.status CheckConstraint diverges from VALIDATION_STATUSES"
    )


def test_dpv_map_values_are_subset() -> None:
    extra = set(_DPV_TO_STATUS.values()) - set(VALIDATION_STATUSES)
    assert not extra, f"DPV→status map emits statuses absent from VALIDATION_STATUSES: {extra}"


def test_status_doc_dpv_column_matches_map() -> None:
    assert _status_doc_dpv_map() == _DPV_TO_STATUS, (
        "docs/VALIDATION-STATUS.md DPV column diverges from _DPV_TO_STATUS"
    )


def test_providers_doc_dpv_table_matches_map() -> None:
    assert _providers_doc_dpv_map() == _DPV_TO_STATUS, (
        "docs/VALIDATION-PROVIDERS.md DPV status mapping diverges from _DPV_TO_STATUS"
    )


def test_vs_meta_keys_match_catalogue() -> None:
    assert set(VS_META) == set(VALIDATION_STATUSES), (
        "admin VS_META keys diverge from VALIDATION_STATUSES"
    )


def test_every_status_is_documented() -> None:
    documented = _documented_statuses()
    missing = [s for s in VALIDATION_STATUSES if s not in documented]
    assert not missing, (
        f"statuses defined in code but absent from docs/VALIDATION-STATUS.md: {missing}"
    )


def test_every_documented_status_exists_in_code() -> None:
    orphaned = [s for s in _documented_statuses() if s not in set(VALIDATION_STATUSES)]
    assert not orphaned, (
        f"statuses documented but absent from core/validation_status.py: {orphaned}"
    )
