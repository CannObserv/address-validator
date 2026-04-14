"""Integration test — validate_config() wired into FastAPI lifespan startup."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from address_validator.main import app


class TestLifespanValidateConfig:
    def test_misconfigured_usps_aborts_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Service must refuse to start when USPS credentials are missing."""
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
        monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)

        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"), TestClient(app):
            pass  # pragma: no cover

    def test_misconfigured_google_aborts_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Service must refuse to start when Google rate limit config is invalid."""
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPM", "0")

        with pytest.raises(ValueError, match="GOOGLE_RATE_LIMIT_RPM"), TestClient(app):
            pass  # pragma: no cover

    def test_valid_none_provider_starts_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Service must start without error when provider is none."""
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)

        # The nested TestClient creates its own anyio event loop. The shared
        # AsyncEngine singleton was created in the session client's loop.
        # Using that engine (or disposing it) from a different loop raises
        # "Future attached to a different loop". We isolate the nested lifespan
        # from the shared engine by patching init/close/get to no-ops so that
        # app.state.engine is set to None (the except RuntimeError branch in
        # main.py lifespan). This test asserts only startup config validity;
        # engine lifecycle and audit behaviour are covered elsewhere.
        with (
            patch("address_validator.db.engine.init_engine", AsyncMock()),
            patch("address_validator.db.engine.close_engine", AsyncMock()),
            patch(
                "address_validator.db.engine.get_engine",
                side_effect=RuntimeError("isolated for lifespan test"),
            ),
            patch("address_validator.middleware.audit.write_audit_row", AsyncMock()),
            patch("address_validator.middleware.audit.write_training_candidate", AsyncMock()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200
