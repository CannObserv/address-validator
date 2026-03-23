"""Shared pytest fixtures for the address-validator test suite."""

import os

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
def client(api_key: str) -> TestClient:
    """A synchronous HTTPX test client wired to the FastAPI app.

    Session-scoped so the app (and its import-time side-effects) is only
    initialised once per test run.
    """
    return TestClient(app, headers={"X-API-Key": api_key})


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
