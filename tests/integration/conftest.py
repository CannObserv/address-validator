"""Integration test suite configuration.

All tests in this directory exercise the full HTTP stack (real FastAPI app
via TestClient with lifespan). They are slower than unit tests and require
the test database.

Run integration tests only:
    uv run pytest -m integration

Skip integration tests:
    uv run pytest -m "not integration"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import address_validator.db.engine as _db_engine_module

if TYPE_CHECKING:
    from collections.abc import Generator

    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_app_state(client: TestClient) -> Generator[None, None, None]:
    """Guard against nested TestClient lifespans that call close_engine().

    Some tests in this suite (e.g. test_lifespan.py) start their own
    ``with TestClient(app) as c:`` blocks, which run the FastAPI lifespan
    shutdown hook and call close_engine().  That disposes the shared
    AsyncEngine singleton, breaking subsequent tests that use the
    session-scoped ``client``.

    The nested lifespan also overwrites ``app.state`` (``engine``,
    ``libpostal_client``, ``registry``) on the shared ``app`` and closes the
    objects it installed, so ``app.state`` is snapshotted and restored.

    After each test: if the engine singleton was disposed, reinitialise it
    using the session-scoped client's anyio portal so the new engine is
    created in the same event loop as the asyncpg connection pool.
    """
    # Starlette's State has no public way to enumerate attributes; _state is
    # the dict its __setattr__ writes to.
    state = client.app.state._state
    snapshot = dict(state)
    yield
    state.clear()
    state.update(snapshot)
    if _db_engine_module._engine is None:
        # close_engine() was called by a nested lifespan — reinitialise.
        client.portal.call(_db_engine_module.init_engine)
        client.app.state.engine = _db_engine_module.get_engine()
