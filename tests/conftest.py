"""Shared pytest fixtures for the address-validator test suite."""

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

TEST_API_KEY = "test-api-key-for-pytest"
TEST_CACHE_DSN = (
    "postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test"
)

# Set these before importing the app so the lifespan hook reads safe values.
# Without the DSN guard, sourcing /etc/address-validator/.env in the caller's
# shell would make TestClient connect to the production database and the audit
# middleware would write real rows on every request.
os.environ.setdefault("API_KEY", TEST_API_KEY)
os.environ.setdefault("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)

from address_validator.main import app  # noqa: E402


@pytest.fixture(scope="session")
def api_key() -> str:
    """The API key used by all test HTTP clients."""
    return TEST_API_KEY


@pytest.fixture(scope="session")
def client(api_key: str) -> Generator[TestClient, None, None]:
    """A synchronous HTTPX test client wired to the FastAPI app.

    Session-scoped so the app (and its import-time side-effects) is only
    initialised once per test run.

    Uses the context-manager form so Starlette's lifespan hook runs during
    ``__enter__``, setting ``app.state.api_key`` before any test makes a
    request.  Without this, tests that run in isolation (e.g. via
    ``pytest -k``) receive a 503 instead of the expected response because
    the auth middleware finds ``app.state.api_key is None``.
    """
    with TestClient(app, headers={"X-API-Key": api_key}) as c:
        yield c


@pytest.fixture(scope="session")
def client_no_auth(client: TestClient) -> TestClient:
    """A client with **no** X-API-Key header — for auth rejection tests.

    Shares ``client.portal`` (the same anyio event loop) so that background
    audit writes don't race against asyncpg connections from a different loop.
    Does NOT enter a separate lifespan — the session-scoped ``client`` owns it.
    """
    c = TestClient(app)
    c.portal = client.portal
    return c


@pytest.fixture(scope="session")
def client_bad_auth(client: TestClient) -> TestClient:
    """A client with a **wrong** X-API-Key — for 403 tests.

    Same event-loop sharing rationale as ``client_no_auth``.
    """
    c = TestClient(app, headers={"X-API-Key": "wrong-key"})
    c.portal = client.portal
    return c


@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    """Exe.dev proxy auth headers for admin dashboard tests."""
    return {
        "X-ExeDev-UserID": "test-user-123",
        "X-ExeDev-Email": "admin@test.example.com",
    }
