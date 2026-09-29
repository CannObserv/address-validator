"""Tests for the unit-failure → notifier handler (GH #232).

No request reaches a real notifier: dispatch runs against a fake client, and the
one test that drives the real SDK points it at a closed local port.
"""

from __future__ import annotations

import logging
import socket
import subprocess
from types import SimpleNamespace

import httpx
import notify_unit_failure as nuf
import pytest
from notifier_client import AuthError

CONFIG_ENV = {
    "NOTIFIER_URL": "http://notifier.test:9000",
    "NOTIFIER_API_KEY": "nk_test",
    "NOTIFIER_UNIT_FAILURE_TEMPLATE_ID": "01TEMPLATE00000000000000000",
    "NOTIFIER_UNIT_FAILURE_CHANNEL_IDS": "01CHANNELA0000000000000000, 01CHANNELB0000000000000000",
}
MONITOR_ENV = {
    "MONITOR_SERVICE_RESULT": "exit-code",
    "MONITOR_EXIT_CODE": "exited",
    "MONITOR_EXIT_STATUS": "1",
    "MONITOR_INVOCATION_ID": "9fa5068adc894cab830dcd62dc49a77b",
}
UNIT = "audit-archive.service"


class FakeClient:
    """Stands in for NotifierClient; records calls, returns canned responses."""

    def __init__(self, *, environment="production", status="succeeded", raises=None, **kwargs):
        self.init_kwargs = kwargs
        self.environment = environment
        self.status = status
        self.raises = raises
        self.dispatched: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def health(self):
        if self.raises:
            raise self.raises
        return {"status": "ok", "environment": self.environment}

    async def dispatch(self, **kwargs):
        self.dispatched.append(kwargs)
        attempts = [SimpleNamespace(status="succeeded"), SimpleNamespace(status="failed")]
        if self.status == "succeeded":
            attempts = [SimpleNamespace(status="succeeded")] * 2
        return SimpleNamespace(id="01DISPATCH", status=self.status, attempts=attempts)


def _factory(**fake_kwargs):
    made: list[FakeClient] = []

    def factory(**kwargs):
        client = FakeClient(**fake_kwargs, **kwargs)
        made.append(client)
        return client

    return factory, made


# --- config -----------------------------------------------------------------


def test_load_config_unset_url_is_journal_only():
    assert nuf.load_config({}) is None
    assert nuf.load_config({"NOTIFIER_URL": "  ", "NOTIFIER_API_KEY": "nk_x"}) is None


def test_load_config_parses_channel_list():
    config = nuf.load_config(CONFIG_ENV)
    assert config is not None
    assert config.channel_ids == ["01CHANNELA0000000000000000", "01CHANNELB0000000000000000"]


def test_load_config_partial_names_what_is_missing():
    with pytest.raises(nuf.ConfigError, match="NOTIFIER_UNIT_FAILURE_CHANNEL_IDS"):
        nuf.load_config({**CONFIG_ENV, "NOTIFIER_UNIT_FAILURE_CHANNEL_IDS": " , "})


# --- failure + idempotency ----------------------------------------------------


def test_failure_from_monitor_env():
    failure = nuf.failure_from_env(UNIT, MONITOR_ENV)
    assert failure.summary == "exit-code (exited=1)"
    assert failure.idempotency_key == f"{UNIT}:9fa5068adc894cab830dcd62dc49a77b"


def test_manual_trigger_gets_a_fresh_key_each_time():
    """A replayed key returns the earlier dispatch and delivers nothing (notifier#95)."""
    failure = nuf.failure_from_env(UNIT, {})
    assert failure.summary == "manual"
    first, second = failure.idempotency_key, failure.idempotency_key
    assert first.startswith(f"{UNIT}:manual:")
    assert first != second
    assert len(first) <= 200  # notifier's idempotency_key maxLength


# --- journal tail ------------------------------------------------------------


def _run_returning(stdout="", exc=None):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return run, calls


def test_journal_tail_scopes_to_invocation_at_warning():
    run, calls = _run_returning("line one\n\nline two\n")
    tail = nuf.journal_tail(nuf.failure_from_env(UNIT, MONITOR_ENV), run=run)
    assert tail == "line one\nline two"
    cmd = calls[0]
    assert cmd[cmd.index("-p") + 1] == "warning"
    assert "_SYSTEMD_INVOCATION_ID=9fa5068adc894cab830dcd62dc49a77b" in cmd
    assert "-u" not in cmd


def test_journal_tail_manual_falls_back_to_unit():
    run, calls = _run_returning("")
    tail = nuf.journal_tail(nuf.failure_from_env(UNIT, {}), run=run)
    assert tail == "(no WARNING-or-higher lines for this run)"
    assert calls[0][-2:] == ["-u", UNIT]


def test_journal_tail_truncates_long_lines():
    run, _ = _run_returning("x" * 2000)
    tail = nuf.journal_tail(nuf.failure_from_env(UNIT, MONITOR_ENV), run=run)
    assert len(tail) == nuf.TAIL_LINE_CHARS


@pytest.mark.parametrize(
    "exc", [FileNotFoundError("journalctl"), subprocess.TimeoutExpired("journalctl", 10)]
)
def test_journal_tail_unavailable_does_not_raise(exc):
    run, _ = _run_returning(exc=exc)
    tail = nuf.journal_tail(nuf.failure_from_env(UNIT, MONITOR_ENV), run=run)
    assert tail.startswith("(journal unavailable:")


# --- dispatch ----------------------------------------------------------------


async def test_notify_dispatches_template_with_variables():
    factory, made = _factory()
    config = nuf.load_config(CONFIG_ENV)
    failure = nuf.failure_from_env(UNIT, MONITOR_ENV)

    status = await nuf.notify(config, failure, "tail", host="av", client_factory=factory)

    assert status == "succeeded"
    (client,) = made
    assert client.init_kwargs["base_url"] == "http://notifier.test:9000"
    assert client.init_kwargs["api_key"] == "nk_test"
    (call,) = client.dispatched
    assert call["template_id"] == "01TEMPLATE00000000000000000"
    assert call["channel_ids"] == config.channel_ids
    assert call["variables"] == {
        "unit": UNIT,
        "result": "exit-code (exited=1)",
        "host": "av",
        "journal_tail": "tail",
    }
    assert call["idempotency_key"] == failure.idempotency_key
    assert call["metadata"]["event"] == "unit-failure"


@pytest.mark.parametrize("status", ["partial", "failed"])
async def test_notify_undelivered_status_is_a_warning(status, caplog):
    """202 does not mean delivered: the body's status decides (notifier#70)."""
    factory, _ = _factory(status=status)
    with caplog.at_level(logging.INFO, logger="notify_unit_failure"):
        result = await nuf.notify(
            nuf.load_config(CONFIG_ENV),
            nuf.failure_from_env(UNIT, MONITOR_ENV),
            "tail",
            host="av",
            client_factory=factory,
        )
    assert result == status
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "1 of 2 channels delivered" in record.getMessage()


async def test_notify_refuses_non_production_endpoint():
    factory, made = _factory(environment="development")
    result = await nuf.notify(
        nuf.load_config(CONFIG_ENV),
        nuf.failure_from_env(UNIT, MONITOR_ENV),
        "tail",
        host="av",
        client_factory=factory,
    )
    assert result is None
    assert made[0].dispatched == []


# --- main: always exit 0 ------------------------------------------------------


@pytest.fixture
def no_journal(monkeypatch):
    monkeypatch.setattr(nuf, "journal_tail", lambda failure: "tail")


def test_main_unset_config_is_journal_only(monkeypatch, caplog):
    def boom(**kwargs):
        raise AssertionError("no client when NOTIFIER_URL is unset")

    monkeypatch.setattr(nuf, "NotifierClient", boom)
    with caplog.at_level(logging.INFO, logger="notify_unit_failure"):
        assert nuf.main([UNIT], env=MONITOR_ENV) == 0
    assert "journal only" in caplog.text


def test_main_dispatches(monkeypatch, no_journal):
    factory, made = _factory()
    monkeypatch.setattr(nuf, "NotifierClient", factory)
    assert nuf.main([UNIT], env={**CONFIG_ENV, **MONITOR_ENV}) == 0
    assert len(made[0].dispatched) == 1


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("unreachable"),
        AuthError("bad key", status_code=401),
        RuntimeError("unexpected"),
    ],
    ids=["unreachable", "rejected", "unexpected"],
)
def test_main_fails_open(monkeypatch, no_journal, caplog, exc):
    factory, _ = _factory(raises=exc)
    monkeypatch.setattr(nuf, "NotifierClient", factory)
    with caplog.at_level(logging.WARNING, logger="notify_unit_failure"):
        assert nuf.main([UNIT], env={**CONFIG_ENV, **MONITOR_ENV}) == 0
    assert "the journal line stands" in caplog.text


def test_main_partial_config_fails_open(caplog):
    env = {"NOTIFIER_URL": "http://notifier.test:9000", **MONITOR_ENV}
    with caplog.at_level(logging.WARNING, logger="notify_unit_failure"):
        assert nuf.main([UNIT], env=env) == 0
    assert "ConfigError" in caplog.text


def test_main_bad_usage_still_exits_zero():
    assert nuf.main([], env={}) == 0


def test_main_real_sdk_unreachable_notifier_fails_open(no_journal, caplog):
    """The real client against a closed port: the SDK's own error type is caught."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**CONFIG_ENV, **MONITOR_ENV, "NOTIFIER_URL": f"http://127.0.0.1:{port}"}
    with caplog.at_level(logging.WARNING, logger="notify_unit_failure"):
        assert nuf.main([UNIT], env=env) == 0
    assert "ConnectError" in caplog.text
