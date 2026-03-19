"""Unit tests for ULID request correlation ID — middleware and logging filter."""

import logging
import re

from fastapi.testclient import TestClient

from logging_filter import RequestIdFilter
from middleware.request_id import _request_id_var, get_request_id

# ULID: 26 Crockford base32 characters
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class TestGetRequestId:
    def test_returns_empty_string_outside_request(self) -> None:
        # Reset to default so prior test state doesn't bleed through
        token = _request_id_var.set("")
        try:
            assert get_request_id() == ""
        finally:
            _request_id_var.reset(token)

    def test_returns_current_value_when_set(self) -> None:
        token = _request_id_var.set("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        try:
            assert get_request_id() == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        finally:
            _request_id_var.reset(token)


class TestRequestIdMiddleware:
    def test_response_includes_x_request_id_header(self, client: TestClient) -> None:
        response = client.get("/")
        assert "x-request-id" in response.headers

    def test_x_request_id_is_ulid_format(self, client: TestClient) -> None:
        response = client.get("/")
        rid = response.headers["x-request-id"]
        assert _ULID_RE.match(rid), f"Not a ULID: {rid!r}"

    def test_each_request_gets_unique_id(self, client: TestClient) -> None:
        r1 = client.get("/")
        r2 = client.get("/")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_api_routes_also_include_x_request_id(self, client: TestClient) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St"})
        assert "x-request-id" in response.headers
        assert _ULID_RE.match(response.headers["x-request-id"])


class TestRequestIdFilter:
    def test_injects_request_id_into_log_record(self) -> None:
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        token = _request_id_var.set("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        try:
            filt.filter(record)
        finally:
            _request_id_var.reset(token)
        assert record.request_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # type: ignore[attr-defined]

    def test_injects_empty_string_when_no_request_active(self) -> None:
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        token = _request_id_var.set("")
        try:
            filt.filter(record)
        finally:
            _request_id_var.reset(token)
        assert record.request_id == ""  # type: ignore[attr-defined]

    def test_filter_always_returns_true(self) -> None:
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert filt.filter(record) is True
