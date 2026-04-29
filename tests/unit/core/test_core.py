"""Unit tests for core.errors — APIError and api_error_response."""

import json

from address_validator.core.errors import APIError, api_error_response


class TestApiErrorResponse:
    def test_status_code_and_body(self) -> None:
        exc = APIError(status_code=400, error="bad_input", message="Something wrong.")
        resp = api_error_response(exc)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert body["error"] == "bad_input"
        assert body["message"] == "Something wrong."

    def test_headers_none_produces_no_extra_headers(self) -> None:
        exc = APIError(status_code=400, error="e", message="m")
        resp = api_error_response(exc)
        assert "retry-after" not in resp.headers

    def test_headers_are_forwarded(self) -> None:
        exc = APIError(
            status_code=429,
            error="provider_rate_limited",
            message="Retry later.",
            headers={"Retry-After": "1"},
        )
        resp = api_error_response(exc)
        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "1"
