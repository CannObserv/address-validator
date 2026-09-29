"""Logging setup for the infra timer scripts: real journal priority under systemd (#232).

A plain ``logging.basicConfig`` stream reaches journald at the unit's default
priority (info), ``ERROR:`` lines included, so ``journalctl -p warning`` never
sees them. When stderr *is* the journal, each record is prefixed with its
sd-daemon ``<N>`` priority, which journald strips and records.

Only a record's first line carries the prefix. Continuation lines — traceback
frames, a psycopg ``DETAIL:`` quoting row data — stay at info, so the WARNING+
tail that ``notify_unit_failure.py`` sends off-host never includes them.
"""

from __future__ import annotations

import logging
import os
import sys

FORMAT = "%(levelname)s: %(message)s"

_PRIORITIES = (
    (logging.CRITICAL, 2),
    (logging.ERROR, 3),
    (logging.WARNING, 4),
    (logging.INFO, 6),
)


def _priority(levelno: int) -> int:
    for threshold, priority in _PRIORITIES:
        if levelno >= threshold:
            return priority
    return 7


class JournalPriorityFormatter(logging.Formatter):
    """Prefix each record with its sd-daemon ``<N>`` priority."""

    def format(self, record: logging.LogRecord) -> str:
        return f"<{_priority(record.levelno)}>{super().format(record)}"


def stderr_is_journal() -> bool:
    """True when stderr is the stream systemd connected to journald.

    ``JOURNAL_STREAM`` is ``<dev>:<inode>`` of that stream; a child whose stderr
    was redirected elsewhere inherits the variable but not the stream.
    """
    value = os.environ.get("JOURNAL_STREAM", "")
    try:
        dev, inode = (int(part) for part in value.split(":"))
        stat = os.fstat(sys.stderr.fileno())
    except (ValueError, OSError):
        return False
    return (stat.st_dev, stat.st_ino) == (dev, inode)


def configure_logging(level: int = logging.INFO) -> None:
    """``basicConfig`` with journal priority prefixes when running under systemd."""
    handler = logging.StreamHandler()
    formatter_cls = JournalPriorityFormatter if stderr_is_journal() else logging.Formatter
    handler.setFormatter(formatter_cls(FORMAT))
    logging.basicConfig(level=level, handlers=[handler])
