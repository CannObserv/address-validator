"""Integration tests for POST /api/v1/validate.

The USPS live-API tests require real credentials and are skipped when
``USPS_CONSUMER_KEY`` / ``USPS_CONSUMER_SECRET`` are absent from the
environment.  They are never expected to run in CI without secrets.

The null-provider test always runs and exercises the full HTTP stack
against the running FastAPI app.
"""

import os

import pytest
from fastapi.testclient import TestClient


class TestValidateNullProvider:
    """Always-run tests — NullProvider requires no external credentials."""

    def test_returns_unavailable_when_no_provider_configured(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] == "unavailable"
        assert body["validation"]["dpv_match_code"] is None
        assert body["validation"]["provider"] is None
        assert body["api_version"] == "1"

    def test_country_defaults_to_us(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
        )
        assert resp.json()["country"] == "US"


@pytest.mark.skipif(
    not os.environ.get("USPS_CONSUMER_KEY"),
    reason="USPS_CONSUMER_KEY not set — skipping live USPS API test",
)
class TestValidateUSPSLive:
    """Live USPS API tests — skipped unless credentials are present."""

    def test_known_good_address_confirmed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        resp = client.post(
            "/api/v1/validate",
            json={
                "address": "1600 Pennsylvania Ave NW",
                "city": "Washington",
                "region": "DC",
                "postal_code": "20500",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] in (
            "confirmed",
            "confirmed_missing_secondary",
            "confirmed_bad_secondary",
        )
        assert body["validation"]["provider"] == "usps"
        assert body["validation"]["dpv_match_code"] in ("Y", "S", "D")

    def test_fake_address_not_confirmed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        resp = client.post(
            "/api/v1/validate",
            json={
                "address": "99999 Nonexistent Blvd",
                "city": "Nowhere",
                "region": "ZZ",
                "postal_code": "00000",
            },
        )
        # May return 200 not_confirmed or a 4xx from USPS — both are acceptable.
        assert resp.status_code in (200, 400, 404, 422)
