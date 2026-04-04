"""Shared pytest fixtures for the address-validator test suite."""

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

TEST_API_KEY = "test-api-key-for-pytest"
os.environ.setdefault("API_KEY", TEST_API_KEY)

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


@pytest.fixture()
def client_no_auth() -> TestClient:
    """A client with **no** X-API-Key header — for auth rejection tests."""
    return TestClient(app)


@pytest.fixture()
def client_bad_auth() -> TestClient:
    """A client with a **wrong** X-API-Key — for 403 tests."""
    return TestClient(app, headers={"X-API-Key": "wrong-key"})


@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    """Exe.dev proxy auth headers for admin dashboard tests."""
    return {
        "X-ExeDev-UserID": "test-user-123",
        "X-ExeDev-Email": "admin@test.example.com",
    }
