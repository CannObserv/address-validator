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
import sys

from pythonjsonlogger.json import JsonFormatter

from address_validator.logging_filter import RequestIdFilter


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


def build_stdout_handler() -> logging.StreamHandler:
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


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON stdout handler on the root logger.

    Under uvicorn, ``--log-config src/address_validator/core/log_config.json``
    configures the whole logging tree at boot and this call merely reinstalls an
    equivalent root handler — which is what keeps app logs JSON even if someone
    launches uvicorn without ``--log-config``.
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [build_stdout_handler()]
