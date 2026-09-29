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
``systemctl start unit-failure@<unit>.service``:
    MONITOR_SERVICE_RESULT, MONITOR_EXIT_CODE, MONITOR_EXIT_STATUS, MONITOR_INVOCATION_ID
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from journal_logging import configure_logging
from notifier_client import NotifierClient, NotifierError, RetryConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = logging.getLogger("notify_unit_failure")

EXPECTED_ENVIRONMENT = "production"
TAIL_LINES = 10
TAIL_LINE_CHARS = 500
REQUEST_TIMEOUT_SECONDS = 10.0
# Two attempts at 10s each keeps health + dispatch well inside the unit's TimeoutStartSec=.
RETRY = RetryConfig(max_attempts=2)
JOURNALCTL = "/usr/bin/journalctl"


class ConfigError(ValueError):
    """NOTIFIER_URL is set but the rest of the notifier config is not."""


@dataclass(frozen=True)
class NotifierConfig:
    url: str
    api_key: str
    template_id: str
    channel_ids: list[str]


@dataclass(frozen=True)
class Failure:
    unit: str
    result: str
    invocation_id: str | None
    exit_code: str | None
    exit_status: str | None

    @property
    def summary(self) -> str:
        """``exit-code (exited=1)`` — the service result plus the main process's exit."""
        if self.exit_code:
            return f"{self.result} ({self.exit_code}={self.exit_status or '?'})"
        return self.result

    @property
    def idempotency_key(self) -> str:
        """One dispatch per failed invocation.

        A replayed key returns the earlier dispatch and delivers nothing new, so a
        manual trigger (no invocation ID) needs a fresh key, never a literal.
        """
        if self.invocation_id:
            return f"{self.unit}:{self.invocation_id}"
        return f"{self.unit}:manual:{uuid.uuid4()}"


def load_config(env: Mapping[str, str]) -> NotifierConfig | None:
    """Return the notifier config, or None when NOTIFIER_URL is unset (journal only)."""
    url = env.get("NOTIFIER_URL", "").strip()
    if not url:
        return None
    api_key = env.get("NOTIFIER_API_KEY", "").strip()
    template_id = env.get("NOTIFIER_UNIT_FAILURE_TEMPLATE_ID", "").strip()
    channel_ids = [
        c.strip() for c in env.get("NOTIFIER_UNIT_FAILURE_CHANNEL_IDS", "").split(",") if c.strip()
    ]
    missing = [
        name
        for name, value in (
            ("NOTIFIER_API_KEY", api_key),
            ("NOTIFIER_UNIT_FAILURE_TEMPLATE_ID", template_id),
            ("NOTIFIER_UNIT_FAILURE_CHANNEL_IDS", channel_ids),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"NOTIFIER_URL is set but {', '.join(missing)} is not")
    return NotifierConfig(url, api_key, template_id, channel_ids)


def failure_from_env(unit: str, env: Mapping[str, str]) -> Failure:
    return Failure(
        unit=unit,
        result=env.get("MONITOR_SERVICE_RESULT") or "manual",
        invocation_id=env.get("MONITOR_INVOCATION_ID") or None,
        exit_code=env.get("MONITOR_EXIT_CODE") or None,
        exit_status=env.get("MONITOR_EXIT_STATUS") or None,
    )


def journal_tail(
    failure: Failure,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """The failed run's last WARNING-or-higher journal lines.

    The priority filter is the PII guard: no address content is logged at
    WARNING+ (docs/LOGGING.md), and ``journal_logging`` keeps traceback lines at
    info. Scoped to the failed invocation when systemd names it.
    """
    selector = (
        [f"_SYSTEMD_INVOCATION_ID={failure.invocation_id}"]
        if failure.invocation_id
        else ["-u", failure.unit]
    )
    cmd = [JOURNALCTL, "--no-pager", "--quiet", "-o", "short-iso", "-p", "warning"]
    cmd += ["-n", str(TAIL_LINES), *selector]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(journal unavailable: {type(exc).__name__})"
    lines = [line[:TAIL_LINE_CHARS] for line in proc.stdout.splitlines() if line.strip()]
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
    async with (client_factory or NotifierClient)(
        base_url=config.url,
        api_key=config.api_key,
        retry_config=RETRY,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as client:
        environment = (await client.health()).get("environment")
        if environment != EXPECTED_ENVIRONMENT:
            logger.warning(
                "notifier at NOTIFIER_URL reports environment=%s, expected %s; not dispatching",
                environment,
                EXPECTED_ENVIRONMENT,
            )
            return None
        result = await client.dispatch(
            template_id=config.template_id,
            channel_ids=config.channel_ids,
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
        )
    # 202 does not mean delivered: judge by the body's status (notifier#70).
    status = str(result.status)
    if status == "succeeded":
        logger.info("notifier dispatch %s for %s: succeeded", result.id, failure.unit)
    else:
        logger.warning(
            "notifier dispatch %s for %s: %s (%d of %d channels delivered)",
            result.id,
            failure.unit,
            status,
            sum(str(a.status) == "succeeded" for a in result.attempts),
            len(result.attempts),
        )
    return status


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
        asyncio.run(notify(config, failure, journal_tail(failure), host=socket.gethostname()))
    except (ConfigError, NotifierError, httpx.HTTPError) as exc:
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
