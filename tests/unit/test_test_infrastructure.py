"""Verify test infrastructure env-var isolation.

These tests are meta — they check that the test suite itself is correctly
isolated from production so that running pytest with a production shell env
(e.g. after sourcing /etc/address-validator/.env) cannot write to the
production database.
"""

import os

_PROD_DB = "address_validator\n"  # not address_validator_test


def test_validation_cache_dsn_points_to_test_database() -> None:
    """conftest.py must pre-set VALIDATION_CACHE_DSN to the test database.

    If VALIDATION_CACHE_DSN is unset or points to the production DB the audit
    middleware will fire-and-forget real rows on every TestClient request.
    """
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "")
    assert "address_validator_test" in dsn, (
        f"VALIDATION_CACHE_DSN={dsn!r} does not contain 'address_validator_test'. "
        "tests/conftest.py must set this before importing app so that the "
        "TestClient lifespan never touches the production database."
    )


def test_validation_cache_dsn_not_production_database() -> None:
    """VALIDATION_CACHE_DSN must not be the production database name."""
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "")
    # Production DB name ends with 'address_validator' (no _test suffix)
    assert not dsn.endswith("/address_validator"), (
        f"VALIDATION_CACHE_DSN={dsn!r} appears to point at production. "
        "Tests must use address_validator_test."
    )
