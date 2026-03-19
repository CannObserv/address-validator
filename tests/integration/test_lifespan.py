"""Integration test — validate_config() wired into FastAPI lifespan startup."""

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
        """Service must refuse to start when Google API key is missing."""
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        with pytest.raises(ValueError, match="GOOGLE_API_KEY"), TestClient(app):
            pass  # pragma: no cover

    def test_valid_none_provider_starts_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Service must start without error when provider is none."""
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)

        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200
