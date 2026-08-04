"""Structured JSON logging — the single formatter definition for the process.

Cohort shape (gregoryfoster/skills#69, #81) plus this service's ``request_id``
correlation field.  Two consumers share :func:`build_json_formatter`:

* :func:`configure_logging` — the app's own root logger, for entry points that
  do **not** run under uvicorn (tests, CLI scripts) and as a safety net when
  someone launches uvicorn without ``--log-config``.
* ``core/log_config.json`` — uvicorn's ``--log-config``, via the dictConfig
  ``"()"`` factory key, so ``uvicorn``/``uvicorn.access``/``uvicorn.error``
  serialize with the identical schema instead of plain text.

Keeping both on one factory is what prevents the mixed plain/JSON journald
output the rest of the cohort had to unpick.
"""

import logging
import os
import sys
from typing import TextIO

from pythonjsonlogger.json import JsonFormatter

from address_validator.logging_filter import RequestIdFilter

#: Env var controlling the app-logger verbosity. Uvicorn's own three loggers are
#: pinned by ``log_config.json`` and steered by uvicorn's ``--log-level`` flag,
#: which never touches root — see :func:`resolve_log_level`.
LOG_LEVEL_ENV = "LOG_LEVEL"

DEFAULT_LOG_LEVEL = logging.INFO

#: Third-party loggers pinned regardless of ``LOG_LEVEL``.
#:
#: ``httpx`` logs the full request URL at INFO, and the libpostal sidecar call is
#: ``GET /parse?address=<the user's address>`` — so an unpinned ``httpx`` writes
#: Canadian address content verbatim into every log line, which AGENTS.md
#: forbids at INFO or above. ``httpcore`` dumps connection and header detail at
#: DEBUG for the same requests.
#:
#: The pin is deliberately **not** derived from ``LOG_LEVEL``: raising app
#: verbosity to debug a parse must not be able to reopen a PII leak. Mirrored in
#: ``log_config.json`` so the uvicorn boot path applies it too.
PINNED_LOGGER_LEVELS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
}


def build_json_formatter() -> JsonFormatter:
    """Build the JSON formatter used by every handler in the process.

    Field set is ``{timestamp, level, logger, message, request_id}``.  The first
    four match structlog's defaults so a later structlog migration doesn't churn
    downstream consumers; ``request_id`` carries the ULID set by
    :class:`~address_validator.logging_filter.RequestIdFilter`.

    Keys must be named in the fmt: a bare ``JsonFormatter()`` defaults to
    ``"%(message)s"`` and emits records with no level, logger, or timestamp
    (skills#69).  Records reaching a handler that lacks the filter render
    ``request_id: null`` rather than raising.
    """
    return JsonFormatter(
        "%(levelname)s %(name)s %(message)s %(request_id)s",
        timestamp=True,
        rename_fields={"levelname": "level", "name": "logger"},
    )


def resolve_log_level(raw: str | None = None) -> tuple[int, str | None]:
    """Resolve the app log level from ``LOG_LEVEL``.

    Returns ``(level, rejected)`` — *rejected* is the offending string when the
    env var held something unparseable, else ``None``.  Unset or unparseable
    both fall back to ``INFO``: a typo in ``LOG_LEVEL`` must not take the
    service down at boot, but it also must not pass silently, so the caller
    logs a warning once logging is up.
    """
    value = os.environ.get(LOG_LEVEL_ENV, "") if raw is None else raw
    name = value.strip().upper()
    if not name:
        return DEFAULT_LOG_LEVEL, None
    # NOTSET is in getLevelNamesMapping() but means "delegate to parent" — on the
    # root logger that resolves to 0, i.e. emit everything from every library.
    # An operator writing LOG_LEVEL=NOTSET means "no preference", not "firehose",
    # so treat it as a rejected value rather than honouring it.
    if name == "NOTSET":
        return DEFAULT_LOG_LEVEL, value
    level = logging.getLevelNamesMapping().get(name)
    if level is None:
        return DEFAULT_LOG_LEVEL, value
    return level, None


def build_stdout_handler() -> logging.StreamHandler[TextIO]:
    """A stdout handler carrying the JSON formatter *and* the request-ID filter.

    The filter belongs on the **handler**, not on a logger: a logger-level
    filter only sees records emitted through that logger, so records
    propagating up from ``address_validator.*`` (and uvicorn's own loggers)
    would never get ``request_id`` attached.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(build_json_formatter())
    handler.addFilter(RequestIdFilter())
    return handler


def configure_logging(level: int | None = None) -> None:
    """Install the JSON stdout handler on the root logger.

    *level* defaults to :envvar:`LOG_LEVEL` (``INFO`` when unset).  This runs at
    ``main`` import time — i.e. **after** uvicorn's ``--log-config`` dictConfig —
    so it is what actually decides the app-logger verbosity in production.
    ``log_config.json``'s ``root.level`` only governs the handful of lines
    uvicorn emits before it imports the app; uvicorn's ``--log-level`` flag
    steers uvicorn's own three loggers and never touches root.

    Under uvicorn this call also reinstalls an equivalent root handler, which is
    what keeps app logs JSON even if someone launches without ``--log-config``.

    :data:`PINNED_LOGGER_LEVELS` is applied last and is not affected by *level*.
    """
    rejected: str | None = None
    if level is None:
        level, rejected = resolve_log_level()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [build_stdout_handler()]

    for name, pinned in PINNED_LOGGER_LEVELS.items():
        logging.getLogger(name).setLevel(pinned)

    if rejected is not None:
        logging.getLogger(__name__).warning(
            "unrecognized %s=%r — falling back to INFO", LOG_LEVEL_ENV, rejected
        )
