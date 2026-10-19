"""sd-daemon journal priority: the one level map for the app and the infra scripts.

A line written to a journal stream is filed at the unit's default priority
(info) unless it starts with ``<N>``, which journald strips and records. Two
writers prefix their records: the app's JSON formatter (``core/logging.py``,
#252) and the infra scripts' text formatter (``infra/journal_logging.py``,
#232). Both take the map and the stream test from here.

Stdlib only: ``infra/notify_unit_failure.py`` imports this through
``journal_logging`` on the alert path, where nothing else of the app may load.
"""

from __future__ import annotations

import logging
import os
from typing import TextIO

PRIORITIES = (
    (logging.CRITICAL, 2),
    (logging.ERROR, 3),
    (logging.WARNING, 4),
    (logging.INFO, 6),
)


def priority(levelno: int) -> int:
    """The sd-daemon priority for a logging level; anything below INFO is debug (7)."""
    for threshold, value in PRIORITIES:
        if levelno >= threshold:
            return value
    return 7


def stream_is_journal(stream: TextIO | None) -> bool:
    """True when *stream* is the one systemd connected to journald.

    ``JOURNAL_STREAM`` is ``<dev>:<inode>`` of the unit's stdout/stderr stream.
    A child whose stream was redirected elsewhere inherits the variable but not
    the stream, so the match is on the descriptor itself, not on the variable.
    *stream* may be ``None``: Python sets ``sys.stdout`` so when fd 1 is closed
    at start, and this runs at boot, so it must not raise.
    """
    value = os.environ.get("JOURNAL_STREAM", "")
    try:
        dev, inode = (int(part) for part in value.split(":"))
        stat = os.fstat(stream.fileno())
    except (AttributeError, ValueError, OSError):
        return False
    return (stat.st_dev, stat.st_ino) == (dev, inode)
