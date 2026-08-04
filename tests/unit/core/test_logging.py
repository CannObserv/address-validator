"""Structured-log contract tests (GH #185, skills#69, skills#81).

Pins five things:

1. **The PII guard.** ``httpx``/``httpcore`` stay at WARNING regardless of
   ``LOG_LEVEL``, and ``log_config.json`` mirrors the pin so the uvicorn boot
   path applies it too. The libpostal sidecar is called as
   ``GET /parse?address=<user address>`` and httpx logs full request URLs at
   INFO, so an unpinned httpx writes address content into every log line.
2. App records render as JSON carrying ``{timestamp, level, logger, message,
   request_id}``. A bare ``JsonFormatter()`` derives its keys from the default
   ``"%(message)s"`` fmt and silently drops level, logger, and timestamp.
3. ``LOG_LEVEL`` resolution — the only knob for app-logger verbosity, since
   uvicorn's ``--log-level`` never touches root. Unparseable values (and
   ``NOTSET``) fall back to INFO and report the rejected string.
4. ``core/log_config.json`` stays valid under ``dictConfig`` (a malformed file
   fails the service at boot, not in review) and single-sources its formatter
   from ``build_json_formatter`` rather than duplicating the fmt string.
5. A ``uvicorn.access`` record renders through that same formatter — the guard
   against uvicorn's lines regressing to plain text next to JSON app logs —
   and the systemd ``ExecStart`` still passes ``--log-config``.
"""

import json
import logging
import logging.config
from pathlib import Path

import pytest

from address_validator.core.logging import (
    LOG_LEVEL_ENV,
    PINNED_LOGGER_LEVELS,
    build_json_formatter,
    build_stdout_handler,
    configure_logging,
    resolve_log_level,
)
from address_validator.logging_filter import RequestIdFilter
from address_validator.middleware.request_id import _request_id_var

_REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_CONFIG_PATH = _REPO_ROOT / "src/address_validator/core/log_config.json"
SERVICE_UNIT_PATH = _REPO_ROOT / "infra/address-validator.service"

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_root_logger(monkeypatch: pytest.MonkeyPatch):
    """Save/restore root handlers and level around configure_logging().

    Also clears any ambient ``LOG_LEVEL``: a value exported in the developer's
    shell would otherwise change the level `configure_logging()` resolves and
    silently suppress the records these tests parse (CI, which exports nothing,
    would not reproduce it). Tests that want a specific level set it themselves.
    """
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_pinned = {n: logging.getLogger(n).level for n in PINNED_LOGGER_LEVELS}
    yield root
    root.handlers, root.level = saved_handlers, saved_level
    for name, level in saved_pinned.items():
        logging.getLogger(name).setLevel(level)


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


class TestLogLevel:
    """`LOG_LEVEL` is the only knob for app-logger verbosity — uvicorn's
    --log-level reaches uvicorn.error/access/asgi and never root."""

    def test_defaults_to_info_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
        assert resolve_log_level() == (logging.INFO, None)

    def test_blank_and_whitespace_are_treated_as_unset(self) -> None:
        assert resolve_log_level("") == (logging.INFO, None)
        assert resolve_log_level("   ") == (logging.INFO, None)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("DEBUG", logging.DEBUG),
            ("debug", logging.DEBUG),
            ("  WaRnInG  ", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ],
    )
    def test_parses_level_names_case_insensitively(self, raw: str, expected: int) -> None:
        assert resolve_log_level(raw) == (expected, None)

    def test_unrecognized_value_falls_back_to_info_and_reports(self) -> None:
        """A typo must not take the service down at boot — but it must not pass
        silently either, so the offending string comes back for the warning."""
        assert resolve_log_level("VERBOSE") == (logging.INFO, "VERBOSE")

    def test_notset_is_rejected_rather_than_honoured(self) -> None:
        """NOTSET is a real name in getLevelNamesMapping(), but on root it means
        level 0 — emit everything from every library. An operator writing it
        means "no preference", so it must land on INFO and be reported."""
        assert resolve_log_level("NOTSET") == (logging.INFO, "NOTSET")

    def test_env_var_drives_configure_logging(
        self, monkeypatch: pytest.MonkeyPatch, restore_root_logger
    ) -> None:
        monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_explicit_level_argument_wins_over_env(
        self, monkeypatch: pytest.MonkeyPatch, restore_root_logger
    ) -> None:
        monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
        configure_logging(logging.ERROR)
        assert logging.getLogger().level == logging.ERROR

    def test_bad_env_var_warns_once_logging_is_up(
        self, monkeypatch: pytest.MonkeyPatch, capsys, restore_root_logger
    ) -> None:
        monkeypatch.setenv(LOG_LEVEL_ENV, "LOUD")
        configure_logging()

        assert logging.getLogger().level == logging.INFO
        warning = json.loads(capsys.readouterr().out)
        assert warning["level"] == "WARNING"
        assert "LOUD" in warning["message"]


class TestPiiPinnedLoggers:
    """`httpx` logs the full request URL at INFO, and the libpostal sidecar call
    is `GET /parse?address=<user address>` — so an unpinned httpx writes address
    content into every line, which AGENTS.md forbids at INFO+."""

    #: Shaped exactly like the real leaked line (see libpostal_client.py:96).
    LEAK = (
        "HTTP Request: GET http://localhost:4400/parse?address=4711+Yonge+Street"
        '+Apt+1203%2C+Toronto+ON+M2N+6K8 "HTTP/1.1 200 OK"'
    )

    def test_address_bearing_httpx_record_is_suppressed(self, capsys, restore_root_logger) -> None:
        configure_logging()
        logging.getLogger("httpx").info(self.LEAK)

        out = capsys.readouterr().out
        assert out == "", f"address content reached the log: {out!r}"

    def test_pin_survives_log_level_debug(
        self, monkeypatch: pytest.MonkeyPatch, capsys, restore_root_logger
    ) -> None:
        """Raising app verbosity to debug a parse must not reopen the leak."""
        monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
        configure_logging()

        logging.getLogger("httpx").info(self.LEAK)
        logging.getLogger("httpcore.connection").debug("connect_tcp.started host=...")

        assert capsys.readouterr().out == ""

    def test_pinned_loggers_still_report_real_problems(self, capsys, restore_root_logger) -> None:
        """WARNING, not silence — a sidecar failure must still surface."""
        configure_logging()
        logging.getLogger("httpx").warning("sidecar unreachable")

        assert json.loads(capsys.readouterr().out)["message"] == "sidecar unreachable"

    def test_log_config_mirrors_the_pins(self) -> None:
        """The uvicorn boot path must apply the same pins, or the leak reopens
        for every line logged before `main` is imported."""
        loggers = json.loads(LOG_CONFIG_PATH.read_text())["loggers"]
        for name, level in PINNED_LOGGER_LEVELS.items():
            assert loggers[name]["level"] == logging.getLevelName(level)


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
            #
            # Residual hazard (benign today, but read this before debugging a
            # weird capture failure downstream): dictConfig's non-incremental
            # path runs _clearExistingHandlers(), which calls logging.shutdown()
            # over every handler registered process-wide — pytest's capture
            # handlers included. The handler objects restored below are
            # therefore *closed*. That is harmless for StreamHandler (close()
            # does not touch the underlying stream), which is why the suite is
            # stable, but a handler type with real close() semantics would
            # break here.
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
