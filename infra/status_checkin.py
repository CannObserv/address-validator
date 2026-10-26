"""Report a backup run to its co-status dead-man monitor (#240).

Ported from CannObserv/provisioner's ``checkin.py`` (cohort pattern, its
``docs/BACKUP.md``). A backup that fails loudly still says nothing when it stops
running: a disabled timer, a dead VM and a run killed by its timeout produce no
failure and no traffic. So every run that finishes checks in, ``ok`` or
``alert``, and co-status alarms when none arrives within the monitor's interval
plus grace (``docs/RECOVERY.md``).

``POST {base}/api/v1/monitors/{id}/checkin``, ``X-API-Key`` (not ``Bearer``),
body ``{"status", "variables", "metadata"}``. An ``alert`` carries
``metadata.fault``, so a change of fault reports at once. A replay overwrites
the previous check-in, so one retry is safe.

- **Never raises, never changes the exit status.** A monitoring path that fails
  the job it watches trains an operator to ignore both; a lost ``ok`` is the
  monitor's silence to report.
- **The key is a credential** (``LoadCredential=checkin-key:``), never an
  environment variable or argv. It is the liveness probe's tenant key
  (``/etc/address-validator/status-checkin.key``); both monitors share the tenant.
- **Unconfigured until the monitor id is set.** No id is a warning every run,
  nothing sent. An id without the base URL or the key is an ERROR naming what is
  missing, nothing sent.
- Errors are logged by exception type, never by message, which may quote the key.

Env vars:
    BACKUP_MONITOR_ID          co-status monitor ULID (unset = unconfigured)
    BACKUP_CHECKIN_BASE_URL    co-status base URL, ``http://status:9000`` (tailnet)
    CREDENTIALS_DIRECTORY      set by systemd; holds ``checkin-key``
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = logging.getLogger("status_checkin")

BASE_URL_ENV = "BACKUP_CHECKIN_BASE_URL"
MONITOR_ID_ENV = "BACKUP_MONITOR_ID"
CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"
KEY_CREDENTIAL = "checkin-key"
_KEY_LABEL = f"the {KEY_CREDENTIAL} credential"
# A tenant key is a short token; anything larger is the wrong file.
_KEY_MAX_BYTES = 4096
# co-status answers within its own 8 s budget.
TIMEOUT_SECONDS = 10.0
# One immediate retry on a transport error or a 5xx.
_ATTEMPTS = 2
# Monitor ids are ULIDs; the value becomes a URL path segment.
_MONITOR_ID = re.compile(r"^[0-9A-Za-z]+$")
_HTTP_OK_MIN, _HTTP_OK_MAX, _HTTP_SERVER_ERROR = 200, 299, 500


def _http_post(url: str, payload: dict, headers: dict, timeout: float) -> int:
    """One POST, returning the status code. The seam the tests replace."""
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, json=payload, headers=headers).status_code


def _read_key(environ: Mapping[str, str]) -> str:
    """The key credential's first line, whitespace dropped; "" when there is none."""
    directory = environ.get(CREDENTIALS_DIRECTORY_ENV, "").strip()
    if not directory:
        return ""
    try:
        with (Path(directory) / KEY_CREDENTIAL).open("rb") as handle:
            raw = handle.read(_KEY_MAX_BYTES)
    except FileNotFoundError:
        return ""
    lines = raw.decode(errors="replace").splitlines()
    return re.sub(r"\s", "", lines[0]) if lines else ""


def _is_http_base(base: str) -> bool:
    """An http(s) URL with a host and, if any, a numeric port."""
    try:
        parts = urlsplit(base)
        _ = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def post_checkin(
    status: Literal["ok", "alert"],
    variables: dict,
    *,
    environ: Mapping[str, str],
    fault: str | None = None,
    post: Callable[[str, dict, dict, float], int] = _http_post,
) -> bool:
    """Report this run; return whether the check-in landed. Never raises."""
    try:
        return _post_checkin(status, variables, environ=environ, fault=fault, post=post)
    except Exception as exc:
        logger.error("check-in failed (%s): not checking in", type(exc).__name__)
        return False


def _post_checkin(  # noqa: PLR0911 — one return per refusal, each logged
    status: Literal["ok", "alert"],
    variables: dict,
    *,
    environ: Mapping[str, str],
    fault: str | None,
    post: Callable[[str, dict, dict, float], int],
) -> bool:
    monitor_id = environ.get(MONITOR_ID_ENV, "").strip()
    if not monitor_id:
        logger.warning("check-in not configured: a stopped backup will not be noticed")
        return False
    base = environ.get(BASE_URL_ENV, "").strip()
    key = _read_key(environ)
    missing = [name for name, there in ((BASE_URL_ENV, base), (_KEY_LABEL, key)) if not there]
    if missing:
        logger.error("check-in half-configured, missing %s: not checking in", ", ".join(missing))
        return False
    if not _is_http_base(base):
        logger.error("%s isn't an http(s) URL with a host: not checking in", BASE_URL_ENV)
        return False
    if not _MONITOR_ID.match(monitor_id):
        logger.error("%s isn't a bare monitor id: not checking in", MONITOR_ID_ENV)
        return False

    url = f"{base.rstrip('/')}/api/v1/monitors/{monitor_id}/checkin"
    payload: dict = {"status": status, "variables": variables}
    if fault:
        payload["metadata"] = {"fault": fault}
    failure = ""
    for _ in range(_ATTEMPTS):
        try:
            code = post(url, payload, {"X-API-Key": key}, TIMEOUT_SECONDS)
        except httpx.TransportError as exc:
            failure = type(exc).__name__
            continue
        if _HTTP_OK_MIN <= code <= _HTTP_OK_MAX:
            logger.info("checked in %s", status)
            return True
        if code < _HTTP_SERVER_ERROR:
            # 401/403/404: the key or the id. 422: an alert whose template can't render.
            logger.error("check-in refused with HTTP %d: check the monitor id and key", code)
            return False
        failure = f"HTTP {code}"
    logger.error("check-in failed (%s): not checking in", failure)
    return False
