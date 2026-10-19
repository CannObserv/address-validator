#!/usr/bin/env python3
"""Push a failed unit to notifier; the journal line stays the fallback (#232).

The second ``ExecStart=`` of ``infra/unit-failure@.service``. The first — the
``logger -t unit-failure`` crit line — has already run, so nothing here is
load-bearing for the fallback. The unit also prefixes this command with ``-``:
fail-open is enforced by systemd, not only by the catch-all in ``main()``.

Usage:
    notify_unit_failure.py <unit>

Env vars (``/etc/address-validator/.env``):
    NOTIFIER_URL                        notifier base URL; unset = journal only
    NOTIFIER_API_KEY                    tenant ``nk_…`` key
    NOTIFIER_UNIT_FAILURE_TEMPLATE_ID   stored template ULID (``publish_notifier_template.py``)
    NOTIFIER_UNIT_FAILURE_CHANNEL_IDS   comma-separated channel ULIDs

Set by systemd (>= 251) for an ``OnFailure=`` handler; absent on a manual
run of ``unit-failure@<unit>.service``:
    MONITOR_SERVICE_RESULT, MONITOR_EXIT_CODE, MONITOR_EXIT_STATUS, MONITOR_INVOCATION_ID
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from journal_logging import configure_logging
from notifier_client import NotifierClient, NotifierError
from notifier_dispatch import ConfigError, NotifierConfig, dispatch
from notifier_dispatch import load_config as _load_config

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

logger = logging.getLogger("notify_unit_failure")

TEMPLATE_VAR = "NOTIFIER_UNIT_FAILURE_TEMPLATE_ID"
TAIL_LINES = 10
TAIL_LINE_CHARS = 500
# httpx timeouts are per phase, so attempts alone do not bound a run. This does,
# with the 10s journalctl call still inside the unit's TimeoutStartSec=90.
RUN_BUDGET_SECONDS = 60
JOURNALCTL = "/usr/bin/journalctl"


@dataclass(frozen=True)
class Failure:
    unit: str
    result: str
    invocation_id: str | None
    exit_code: str | None
    exit_status: str | None
    # One dispatch per failed invocation. A replayed key returns the earlier
    # dispatch and delivers nothing, so a manual trigger (no invocation ID) gets a
    # fresh key — fixed once here, so every reader sees the key that was sent.
    idempotency_key: str

    @property
    def summary(self) -> str:
        """``exit-code (exited=1)`` — the service result plus the main process's exit."""
        if self.exit_code:
            return f"{self.result} ({self.exit_code}={self.exit_status or '?'})"
        return self.result


def load_config(env: Mapping[str, str]) -> NotifierConfig | None:
    """Return the notifier config, or None when NOTIFIER_URL is unset (journal only)."""
    return _load_config(env, template_var=TEMPLATE_VAR)


def failure_from_env(unit: str, env: Mapping[str, str]) -> Failure:
    invocation_id = env.get("MONITOR_INVOCATION_ID") or None
    return Failure(
        unit=unit,
        result=env.get("MONITOR_SERVICE_RESULT") or "manual",
        invocation_id=invocation_id,
        exit_code=env.get("MONITOR_EXIT_CODE") or None,
        exit_status=env.get("MONITOR_EXIT_STATUS") or None,
        idempotency_key=(
            f"{unit}:{invocation_id}" if invocation_id else f"{unit}:manual:{uuid.uuid4()}"
        ),
    )


def _message_text(value: object) -> str:
    """``MESSAGE`` as text; ``journalctl -o json`` gives unprintable ones as a byte list."""
    if isinstance(value, list):
        return bytes(value).decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def _render_message(message: str) -> str:
    """The message as the tail may send it — the PII guard for JSON records (#252).

    A JSON record (the API's ``core/logging.py`` formatter) is reduced to
    ``LEVEL logger: <first line of message> [exc_type]``. ``exc_info``,
    ``stack_info`` and every other key are dropped: a traceback's last line
    carries the exception message, and a psycopg one can quote row data
    (``DETAIL: Key (…)=(…)``). Only the message's first line is kept, as
    ``journal_logging`` keeps continuation lines at info: uvicorn logs a failed
    startup's traceback as the message itself. A line that opens like JSON but
    is not such a record is withheld whole. Any other line (timer scripts'
    ``LEVEL: message``, docker and Go text) passes through.
    """
    if not message.lstrip().startswith(("{", "[")):
        return message
    try:
        record = json.loads(message)
    except ValueError:
        record = None
    if not isinstance(record, dict) or "level" not in record:
        return "(JSON record withheld: not a log record)"
    text = str(record.get("message") or "").strip().splitlines()
    line = f"{record['level']} {record.get('logger', '?')}: {text[0] if text else ''}"
    if record.get("exc_type"):
        line += f" [{record['exc_type']}]"
    return line


def _render_entry(entry: dict[str, Any]) -> list[str]:
    """One journal entry as ``short-iso`` would print it, message rendered first."""
    try:
        when = datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1e6, tz=UTC)
        stamp = when.isoformat(timespec="seconds")
    except (KeyError, TypeError, ValueError):
        stamp = "?"
    ident = entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or "?"
    pid = entry.get("SYSLOG_PID") or entry.get("_PID")
    prefix = f"{stamp} {entry.get('_HOSTNAME', '?')} {ident}{f'[{pid}]' if pid else ''}: "
    lines = _message_text(entry.get("MESSAGE")).splitlines()
    return [prefix + _render_message(line) for line in lines if line.strip()]


def journal_tail(
    failure: Failure,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """The failed run's last WARNING-or-higher journal lines.

    Two guards keep address content on the host. The priority filter: nothing
    with address content is logged at WARNING+ (docs/LOGGING.md), and
    ``journal_logging`` keeps traceback lines at info. And the API's JSON
    records, which reach WARNING+ whole with any traceback, pass only the
    allowlisted fields of :func:`_render_message`. ``-o json`` gives the exact
    message boundary for that parse, ``--all`` keeps a message over 4 KiB from
    arriving as null, and lines are truncated only after it. Scoped to the
    failed invocation when systemd names it.
    """
    selector = (
        [f"_SYSTEMD_INVOCATION_ID={failure.invocation_id}"]
        if failure.invocation_id
        else ["-u", failure.unit]
    )
    cmd = [JOURNALCTL, "--no-pager", "--quiet", "-o", "json", "--all", "-p", "warning"]
    cmd += ["-n", str(TAIL_LINES), *selector]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(journal unavailable: {type(exc).__name__})"
    lines: list[str] = []
    for raw in proc.stdout.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict):
            lines += [line[:TAIL_LINE_CHARS] for line in _render_entry(entry)]
    return "\n".join(lines) or "(no WARNING-or-higher lines for this run)"


async def notify(
    config: NotifierConfig,
    failure: Failure,
    tail: str,
    *,
    host: str,
    client_factory: Callable[..., NotifierClient] | None = None,
) -> str | None:
    """Dispatch the failure; return the dispatch status, or None if nothing was sent."""
    return await dispatch(
        config,
        subject=failure.unit,
        variables={
            "unit": failure.unit,
            "result": failure.summary,
            "host": host,
            "journal_tail": tail,
        },
        idempotency_key=failure.idempotency_key,
        metadata={
            "source": "address-validator",
            "event": "unit-failure",
            "unit": failure.unit,
            "invocation_id": failure.invocation_id,
        },
        client_factory=client_factory or NotifierClient,
    )


async def _within_budget(coro: Coroutine[Any, Any, str | None]) -> str | None:
    async with asyncio.timeout(RUN_BUDGET_SECONDS):
        return await coro


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Always returns 0: a failing handler would itself need a failure handler."""
    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if len(args) != 1:
        logger.warning("usage: notify_unit_failure.py <unit>")
        return 0
    unit = args[0]
    try:
        config = load_config(env)
        if config is None:
            logger.info("NOTIFIER_URL unset; %s reported to the journal only", unit)
            return 0
        failure = failure_from_env(unit, env)
        tail = journal_tail(failure)
        asyncio.run(_within_budget(notify(config, failure, tail, host=socket.gethostname())))
    except (ConfigError, NotifierError, httpx.HTTPError, TimeoutError) as exc:
        logger.warning(
            "notifier dispatch for %s failed (%s: %s); the journal line stands",
            unit,
            type(exc).__name__,
            getattr(exc, "status_code", None) or exc,
        )
    except Exception:  # fail-open: this is the alert path's last resort
        logger.warning(
            "notifier dispatch for %s failed unexpectedly; the journal line stands",
            unit,
            exc_info=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
