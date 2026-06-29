"""Unit tests for core.errors — APIError and api_error_response."""

import json

import pytest

from address_validator.core.errors import (
    APIError,
    api_error_response,
    raise_parsing_unavailable,
)


class TestRaiseParsingUnavailable:
    def test_raises_canonical_503_apierror(self) -> None:
        with pytest.raises(APIError) as exc_info:
            raise_parsing_unavailable("CA")
        exc = exc_info.value
        assert exc.status_code == 503
        assert exc.error == "parsing_unavailable"
        assert "currently unavailable" in exc.message
        assert "CA" in exc.message

    def test_message_is_country_specific(self) -> None:
        with pytest.raises(APIError) as exc_info:
            raise_parsing_unavailable("GB")
        assert "Address parsing for GB" in exc_info.value.message

    def test_preserves_cause_chain(self) -> None:
        original = RuntimeError("sidecar down")
        with pytest.raises(APIError) as exc_info:
            raise_parsing_unavailable("CA", original)
        assert exc_info.value.__cause__ is original


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
