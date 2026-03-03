"""Shared pytest fixtures for the address-validator test suite.

Auth handling
-------------
``auth.py`` reads ``API_KEY`` from the environment **at import time** and
raises ``RuntimeError`` if the variable is absent or empty.  To prevent that
from blowing up the test collection we set a sentinel value before any
application module is imported.  The ``monkeypatch`` fixture then overrides
``auth._API_KEY`` at the module level for tests that exercise the auth logic
directly, or overrides the FastAPI dependency for HTTP-level tests.

Import order
------------
``from main import app`` must come *after* ``os.environ.setdefault`` below;
otherwise auth.py raises ``RuntimeError`` at collection time.  This is an
intentional ordering constraint, not a style issue — PLC0415 is suppressed.
"""

import os

import pytest
from fastapi.testclient import TestClient

# Must be set before application modules are imported so auth.py doesn't
# raise at collection time.
TEST_API_KEY = "test-api-key-for-pytest"
os.environ.setdefault("API_KEY", TEST_API_KEY)

# Deferred import: ordering constraint — must follow os.environ.setdefault.
from main import app  # noqa: E402


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
