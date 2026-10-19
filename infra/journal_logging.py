"""Logging setup for the infra timer scripts: real journal priority under systemd (#232).

A plain ``logging.basicConfig`` stream reaches journald at the unit's default
priority (info), ``ERROR:`` lines included, so ``journalctl -p warning`` never
sees them. When stderr *is* the journal, each record is prefixed with its
sd-daemon ``<N>`` priority, which journald strips and records.

Only a record's first line carries the prefix. Continuation lines — traceback
frames, a psycopg ``DETAIL:`` quoting row data — stay at info, so the WARNING+
tail that ``notify_unit_failure.py`` sends off-host never includes them.

The level map and the stream test are shared with the app's JSON formatter
(``address_validator.core.journal``, #252), so every unit needs ``src`` on
``PYTHONPATH``.
"""

from __future__ import annotations

import logging
import sys

from address_validator.core.journal import priority, stream_is_journal

FORMAT = "%(levelname)s: %(message)s"


class JournalPriorityFormatter(logging.Formatter):
    """Prefix each record with its sd-daemon ``<N>`` priority."""

    def format(self, record: logging.LogRecord) -> str:
        return f"<{priority(record.levelno)}>{super().format(record)}"


def stderr_is_journal() -> bool:
    """True when stderr — this module's handler stream — is the journal."""
    return stream_is_journal(sys.stderr)


def configure_logging(level: int = logging.INFO) -> None:
    """``basicConfig`` with journal priority prefixes when running under systemd."""
    handler = logging.StreamHandler()
    formatter_cls = JournalPriorityFormatter if stderr_is_journal() else logging.Formatter
    handler.setFormatter(formatter_cls(FORMAT))
    logging.basicConfig(level=level, handlers=[handler])
