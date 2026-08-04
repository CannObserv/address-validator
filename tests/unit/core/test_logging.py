"""Structured-log contract tests (GH #185, skills#69, skills#81).

Pins three things:

1. App records render as JSON carrying ``{timestamp, level, logger, message,
   request_id}``. A bare ``JsonFormatter()`` derives its keys from the default
   ``"%(message)s"`` fmt and silently drops level, logger, and timestamp.
2. ``core/log_config.json`` stays valid under ``dictConfig`` (a malformed file
   fails the service at boot, not in review) and single-sources its formatter
   from ``build_json_formatter`` rather than duplicating the fmt string.
3. A ``uvicorn.access`` record renders through that same formatter — the guard
   against uvicorn's lines regressing to plain text next to JSON app logs.
"""

import json
import logging
import logging.config
from pathlib import Path

import pytest

from address_validator.core.logging import (
    build_json_formatter,
    build_stdout_handler,
    configure_logging,
)
from address_validator.logging_filter import RequestIdFilter
from address_validator.middleware.request_id import _request_id_var

_REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_CONFIG_PATH = _REPO_ROOT / "src/address_validator/core/log_config.json"
SERVICE_UNIT_PATH = _REPO_ROOT / "infra/address-validator.service"

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_root_logger():
    """Save/restore root handlers and level around configure_logging()."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield root
    root.handlers, root.level = saved_handlers, saved_level


class TestJsonFormatter:
    def test_app_record_renders_structured_fields(self, capsys, restore_root_logger) -> None:
        token = _request_id_var.set("01JQTESTREQUESTID0000000")
        try:
            configure_logging()
            logging.getLogger("address_validator.probe").warning("hello %s", "world")
        finally:
            _request_id_var.reset(token)

        record = json.loads(capsys.readouterr().out)
        assert record["message"] == "hello world"
        assert record["level"] == "WARNING"
        assert record["logger"] == "address_validator.probe"
        assert record["request_id"] == "01JQTESTREQUESTID0000000"
        assert "timestamp" in record

    def test_request_id_empty_outside_request_context(self, capsys, restore_root_logger) -> None:
        """No request in flight: request_id must be present and empty, not absent
        and not a KeyError."""
        # Reset explicitly so a ULID set by an earlier test can't bleed through.
        token = _request_id_var.set("")
        try:
            configure_logging()
            logging.getLogger("address_validator.probe").info("no request context")
        finally:
            _request_id_var.reset(token)

        record = json.loads(capsys.readouterr().out)
        assert record["request_id"] == ""

    def test_formatter_without_filter_defaults_request_id(self) -> None:
        """A record that never passed the filter renders null rather than raising."""
        record = logging.LogRecord(
            name="somewhere.else",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="unfiltered",
            args=None,
            exc_info=None,
        )
        parsed = json.loads(build_json_formatter().format(record))
        assert parsed["request_id"] is None

    def test_propagated_record_carries_request_id(self, capsys, restore_root_logger) -> None:
        """The filter lives on the handler, not a logger — records propagating up
        from a child logger must still get request_id attached (the defect the
        old root-logger addFilter() had)."""
        token = _request_id_var.set("01JQPROPAGATED0000000000")
        try:
            configure_logging()
            logging.getLogger("address_validator.services.deeply.nested").error("boom")
        finally:
            _request_id_var.reset(token)

        assert json.loads(capsys.readouterr().out)["request_id"] == "01JQPROPAGATED0000000000"

    def test_stdout_handler_carries_the_filter(self) -> None:
        handler = build_stdout_handler()
        assert any(isinstance(f, RequestIdFilter) for f in handler.filters)


class TestUvicornLogConfig:
    def test_log_config_is_valid_and_shares_formatter(self) -> None:
        config = json.loads(LOG_CONFIG_PATH.read_text())

        # Single source of truth: the file builds its formatter from the same
        # factory configure_logging() uses, not a duplicated fmt string.
        assert any(
            f.get("()") == "address_validator.core.logging.build_json_formatter"
            for f in config["formatters"].values()
        )
        # The request-ID filter must reach the shared handler.
        assert any(
            f.get("()") == "address_validator.logging_filter.RequestIdFilter"
            for f in config["filters"].values()
        )
        assert "request_id" in config["handlers"]["stdout"]["filters"]
        # All three uvicorn loggers must be present, else they keep the plain default.
        for name in UVICORN_LOGGERS:
            assert name in config["loggers"]
            assert config["loggers"][name]["propagate"] is False

        names = ("", *UVICORN_LOGGERS)
        saved = {
            n: (
                logging.getLogger(n).handlers[:],
                logging.getLogger(n).propagate,
                logging.getLogger(n).level,
            )
            for n in names
        }
        try:
            logging.config.dictConfig(config)  # raises on a malformed config
        finally:
            # Restore level too: dictConfig sets root + uvicorn loggers to INFO,
            # and leaking that into later tests would be an order-dependent flake.
            for n, (handlers, propagate, level) in saved.items():
                lg = logging.getLogger(n)
                lg.handlers, lg.propagate, lg.level = handlers, propagate, level

    def test_shared_formatter_renders_uvicorn_access_record(self) -> None:
        """A uvicorn.access record formats to JSON with the same fields as app
        logs — the request line lands in `message`, not a plain-text handler."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:0", "GET", "/api/v2/health", "1.1", 200),
            exc_info=None,
        )
        parsed = json.loads(build_json_formatter().format(record))
        assert parsed["logger"] == "uvicorn.access"
        assert parsed["level"] == "INFO"
        assert parsed["message"] == '127.0.0.1:0 - "GET /api/v2/health HTTP/1.1" 200'
        assert "timestamp" in parsed


class TestSystemdUnitWiring:
    def test_service_unit_passes_log_config(self) -> None:
        """ExecStart must carry --log-config, else production journald gets the
        mixed plain/JSON output this issue removed."""
        unit = SERVICE_UNIT_PATH.read_text()
        exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        assert "--log-config src/address_validator/core/log_config.json" in exec_start
