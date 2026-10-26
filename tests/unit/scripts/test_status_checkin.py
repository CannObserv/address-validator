"""The backup's co-status check-in (#240): never raises, never leaks its key."""

from __future__ import annotations

import logging

import httpx
import pytest
import status_checkin as sc

MONITOR = "01M5B7W1XK1WPGDZPXTWYTMDPE"
BASE = "http://status:9000"
KEY = "ck_secret-tenant-key"


@pytest.fixture
def creds(tmp_path):
    (tmp_path / sc.KEY_CREDENTIAL).write_text(KEY + "\n")
    return tmp_path


def _env(creds, **overrides):
    env = {
        sc.MONITOR_ID_ENV: MONITOR,
        sc.BASE_URL_ENV: BASE,
        sc.CREDENTIALS_DIRECTORY_ENV: str(creds),
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class Recorder:
    def __init__(self, *codes):
        self.codes = list(codes) or [202]
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append((url, payload, headers, timeout))
        code = self.codes.pop(0)
        if isinstance(code, Exception):
            raise code
        return code


def test_ok_posts_to_the_monitor_with_the_key_header(creds):
    post = Recorder()
    assert sc.post_checkin("ok", {"outcome": "uploaded"}, environ=_env(creds), post=post)
    ((url, payload, headers, timeout),) = post.calls
    assert url == f"{BASE}/api/v1/monitors/{MONITOR}/checkin"
    assert payload == {"status": "ok", "variables": {"outcome": "uploaded"}}
    assert headers == {"X-API-Key": KEY}
    assert timeout == sc.TIMEOUT_SECONDS


def test_alert_carries_its_fault(creds):
    post = Recorder()
    sc.post_checkin("alert", {}, environ=_env(creds), fault="failed", post=post)
    assert post.calls[0][1]["metadata"] == {"fault": "failed"}


def test_key_is_its_first_line_without_whitespace(tmp_path):
    (tmp_path / sc.KEY_CREDENTIAL).write_text(" ck_a b\r\nsecond line\n")
    post = Recorder()
    sc.post_checkin("ok", {}, environ=_env(tmp_path), post=post)
    assert post.calls[0][2] == {"X-API-Key": "ck_ab"}


def test_no_monitor_id_is_unconfigured_and_sends_nothing(creds, caplog):
    post = Recorder()
    with caplog.at_level(logging.WARNING):
        assert not sc.post_checkin(
            "ok", {}, environ=_env(creds, **{sc.MONITOR_ID_ENV: ""}), post=post
        )
    assert post.calls == []
    assert "not configured" in caplog.text


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({sc.BASE_URL_ENV: None}, sc.BASE_URL_ENV),
        ({sc.CREDENTIALS_DIRECTORY_ENV: None}, sc.KEY_CREDENTIAL),
    ],
)
def test_half_configured_is_an_error_naming_what_is_missing(creds, caplog, overrides, named):
    post = Recorder()
    with caplog.at_level(logging.ERROR):
        assert not sc.post_checkin("ok", {}, environ=_env(creds, **overrides), post=post)
    assert post.calls == []
    assert named in caplog.text


def test_missing_key_file_is_half_configured(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        assert not sc.post_checkin("ok", {}, environ=_env(tmp_path), post=Recorder())
    assert "half-configured" in caplog.text


@pytest.mark.parametrize(
    "overrides",
    [{sc.BASE_URL_ENV: "status:9000"}, {sc.MONITOR_ID_ENV: "../tenants/x"}],
)
def test_malformed_config_sends_nothing(creds, overrides):
    post = Recorder()
    assert not sc.post_checkin("ok", {}, environ=_env(creds, **overrides), post=post)
    assert post.calls == []


def test_one_retry_on_a_5xx(creds):
    post = Recorder(503, 202)
    assert sc.post_checkin("ok", {}, environ=_env(creds), post=post)
    assert len(post.calls) == 2


def test_one_retry_on_a_transport_error(creds, caplog):
    post = Recorder(httpx.ConnectError("boom"), httpx.ConnectError("boom"))
    with caplog.at_level(logging.ERROR):
        assert not sc.post_checkin("ok", {}, environ=_env(creds), post=post)
    assert len(post.calls) == 2
    assert "ConnectError" in caplog.text


def test_a_4xx_is_not_retried(creds):
    post = Recorder(401)
    assert not sc.post_checkin("ok", {}, environ=_env(creds), post=post)
    assert len(post.calls) == 1


def test_never_raises_and_never_logs_the_key(creds, caplog):
    def explode(url, payload, headers, timeout):
        raise ValueError(f"bad header {headers['X-API-Key']}")

    with caplog.at_level(logging.DEBUG):
        assert not sc.post_checkin("ok", {}, environ=_env(creds), post=explode)
    assert "ValueError" in caplog.text
    assert KEY not in caplog.text
