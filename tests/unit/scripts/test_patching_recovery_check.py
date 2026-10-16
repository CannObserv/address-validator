"""Tests for the patching-hosts recovery check (GH #311).

The app is an ``httpx.MockTransport`` standing in for port 8000: each request it
answers can write its own ``audit_log`` row into the test DB, as the real audit
middleware would, or not, as a fail-open app does while Postgres is down. No
request reaches the production service or a provider.
"""

from __future__ import annotations

import asyncio
import itertools
import socket
from typing import TYPE_CHECKING

import httpx
import patching_recovery_check as prc
import pytest
from sqlalchemy import text

from tests.conftest import TEST_CACHE_DSN

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

API_KEY = "test-key-not-a-secret"


def _app(engine: AsyncEngine | None, *, statuses=(200, 200), hits=(False, True), seen=None):
    """A fake app: answers with ``statuses``; writes an audit row per request
    with ``cache_hit`` from ``hits`` when ``engine`` is given."""
    ids = itertools.count(1)
    calls = itertools.count()

    async def handler(request: httpx.Request) -> httpx.Response:
        n = next(calls)
        if seen is not None:
            seen.append(request)
        request_id = f"01TESTREQUEST{next(ids):013d}"
        if engine is not None:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO audit_log (timestamp, request_id, client_ip, method,"
                        " endpoint, status_code, cache_hit) VALUES (now(), :rid, '127.0.0.1',"
                        " 'POST', '/api/v2/validate', :code, :hit)"
                    ),
                    {"rid": request_id, "code": statuses[n], "hit": hits[n]},
                )
        return httpx.Response(statuses[n], json={}, headers={"x-request-id": request_id})

    return httpx.MockTransport(handler)


async def _check(engine: AsyncEngine, transport: httpx.MockTransport, **kwargs) -> str | None:
    async with httpx.AsyncClient(transport=transport, base_url=prc.BASE_URL) as client:
        return await prc.check(client, engine, API_KEY, audit_wait_s=0.5, poll_s=0.05, **kwargs)


# --- the check ----------------------------------------------------------------


async def test_recovered_when_both_rows_land_and_the_repeat_hits(db: AsyncEngine):
    assert await _check(db, _app(db)) is None


async def test_repeat_that_misses_the_cache_is_not_recovered(db: AsyncEngine):
    """The cache read path is still failing open: every call goes to a provider."""
    reason = await _check(db, _app(db, hits=(False, False)))
    assert reason is not None
    assert "cache hit" in reason


async def test_missing_audit_rows_are_not_recovered(db: AsyncEngine):
    """A fail-open audit writer: callers get 200s and no row lands."""
    reason = await _check(db, _app(None))
    assert reason is not None
    assert "audit_log" in reason


async def test_a_non_200_stops_before_the_repeat(db: AsyncEngine):
    """A stale pooled connection serves a 503 just after Postgres is ready (#235)."""
    seen: list[httpx.Request] = []
    reason = await _check(db, _app(db, statuses=(503, 200), seen=seen))
    assert reason == "validate #1 returned HTTP 503"
    assert len(seen) == 1


async def test_a_response_without_a_request_id_is_not_recovered(db: AsyncEngine):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    assert await _check(db, transport) == "validate #1 carried no X-Request-ID"


async def test_postgres_down_sends_no_request():
    """While Postgres is down every validate would miss the cache and spend
    provider quota, once a second for the whole outage. So: no request."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    engine = prc.make_engine(f"postgresql+asyncpg://u:p@127.0.0.1:{port}/x")
    seen: list[httpx.Request] = []
    try:
        reason = await _check(engine, _app(None, seen=seen))
    finally:
        await engine.dispose()
    assert reason is not None
    assert reason.startswith("postgres not accepting connections (")
    assert str(port) not in reason, "the DSN never reaches the output"
    assert seen == []


async def test_requests_carry_the_key_and_the_synthetic_address(db: AsyncEngine):
    seen: list[httpx.Request] = []
    await _check(db, _app(db, seen=seen))
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/api/v2/validate")] * 2
    for request in seen:
        assert request.headers["x-api-key"] == API_KEY
        assert prc.ADDRESS.encode() in request.content
        assert prc.ADDRESS not in str(request.url)


# --- config -------------------------------------------------------------------


def test_the_callers_environment_is_ignored(tmp_path: Path, monkeypatch):
    """A shell still exporting the isolated test DSN (AGENTS.md) must not point
    the audit read at another database than the one behind port 8000."""
    monkeypatch.setenv("API_KEY", "from-env")
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://e/e")
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=from-file\nVALIDATION_CACHE_DSN=postgresql+asyncpg://f/f\n")
    assert prc.load_config(env_file) == ("from-file", "postgresql+asyncpg://f/f")


def test_file_is_read_as_systemd_reads_it(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nLOG_LEVEL=INFO\nAPI_KEY='quoted key'\n"
        'VALIDATION_CACHE_DSN="postgresql+asyncpg://u:p@h/d"\n'
    )
    assert prc.load_config(env_file) == ("quoted key", "postgresql+asyncpg://u:p@h/d")


@pytest.mark.parametrize("present", ["", "API_KEY=k\n", "VALIDATION_CACHE_DSN=postgresql://d\n"])
def test_missing_setting_is_a_config_error(tmp_path: Path, present: str):
    env_file = tmp_path / ".env"
    env_file.write_text(present)
    with pytest.raises(prc.ConfigError):
        prc.load_config(env_file)


def test_unreadable_file_is_a_config_error(tmp_path: Path):
    with pytest.raises(prc.ConfigError):
        prc.load_config(tmp_path / "absent")


# --- run: exit status and what it prints ---------------------------------------


def _env_file(tmp_path: Path) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(f"API_KEY={API_KEY}\nVALIDATION_CACHE_DSN={TEST_CACHE_DSN}\n")
    return env_file


async def test_run_exits_0_and_says_recovered(db: AsyncEngine, tmp_path: Path, capsys):
    assert await prc.run(_env_file(tmp_path), transport=_app(db)) == 0
    assert capsys.readouterr().out == "recovered\n"


async def test_run_failure_prints_no_secret_or_address(db: AsyncEngine, tmp_path: Path, capsys):
    env_file = _env_file(tmp_path)
    assert await prc.run(env_file, transport=_app(None), audit_wait_s=0.2) == 1
    out = capsys.readouterr()
    printed = out.out + out.err
    assert printed.startswith("not recovered: ")
    for secret in (API_KEY, TEST_CACHE_DSN, prc.ADDRESS):
        assert secret not in printed


async def test_run_config_error_exits_1(tmp_path: Path, capsys):
    assert await prc.run(tmp_path / "absent") == 1
    assert capsys.readouterr().out.startswith("not recovered: config")


def test_the_whole_check_fits_the_skills_limit():
    """knob.md: a knob command is TERM'd at 60 s and reported only as timed out."""
    assert prc.CHECK_BUDGET_S + prc.DB_CONNECT_TIMEOUT_S < 60
    assert prc.AUDIT_WAIT_S + 2 * prc.HTTP_TIMEOUT_S < prc.CHECK_BUDGET_S


async def test_a_stalled_step_is_reported_inside_the_budget(
    db: AsyncEngine, tmp_path: Path, monkeypatch, capsys
):
    async def stall(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    monkeypatch.setattr(prc, "CHECK_BUDGET_S", 0.2)
    assert await prc.run(_env_file(tmp_path), transport=httpx.MockTransport(stall)) == 1
    assert capsys.readouterr().out == "not recovered: timed out after 0.2s\n"


async def test_an_unusable_dsn_is_one_config_line(tmp_path: Path, capsys):
    """The skill records a knob command's output: one line, never a traceback."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"API_KEY={API_KEY}\nVALIDATION_CACHE_DSN=postgresql+asyncpg://u:s3cret@h:port/d\n"
    )
    assert await prc.run(env_file) == 1
    out = capsys.readouterr()
    assert out.out == "not recovered: config: VALIDATION_CACHE_DSN unusable (ValueError)\n"
    assert "s3cret" not in out.out + out.err
