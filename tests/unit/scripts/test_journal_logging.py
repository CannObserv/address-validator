"""Tests for the infra scripts' journal-priority logging (GH #232)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import journal_logging
import pytest


def _record(level: int, msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("t", level, __file__, 1, msg, None, None)


@pytest.mark.parametrize(
    ("level", "prefix"),
    [
        (logging.CRITICAL, "<2>"),
        (logging.ERROR, "<3>"),
        (logging.WARNING, "<4>"),
        (logging.INFO, "<6>"),
        (logging.DEBUG, "<7>"),
    ],
)
def test_prefix_maps_level_to_sd_daemon_priority(level, prefix):
    formatter = journal_logging.JournalPriorityFormatter(journal_logging.FORMAT)
    out = formatter.format(_record(level))
    assert out == f"{prefix}{logging.getLevelName(level)}: hello"


def test_only_first_line_carries_priority():
    """Traceback/DETAIL continuation lines must stay at info — the WARNING+ tail is off-host."""
    formatter = journal_logging.JournalPriorityFormatter(journal_logging.FORMAT)
    out = formatter.format(_record(logging.ERROR, "failed\nDETAIL: row data"))
    first, second = out.split("\n")
    assert first.startswith("<3>")
    assert not second.startswith("<")


def test_stderr_is_journal_matches_device_and_inode(monkeypatch, tmp_path):
    stream = (tmp_path / "stderr").open("w")
    monkeypatch.setattr(sys, "stderr", stream)
    stat = os.fstat(stream.fileno())
    monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino}")
    assert journal_logging.stderr_is_journal()
    monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino + 1}")
    assert not journal_logging.stderr_is_journal()
    stream.close()


@pytest.mark.parametrize("value", [None, "", "garbage", "1:2:3"])
def test_stderr_is_journal_false_without_valid_stream(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    else:
        monkeypatch.setenv("JOURNAL_STREAM", value)
    assert not journal_logging.stderr_is_journal()


@pytest.mark.parametrize(
    ("journal", "expected"),
    [(True, journal_logging.JournalPriorityFormatter), (False, logging.Formatter)],
)
def test_configure_logging_picks_formatter(monkeypatch, journal, expected):
    captured = {}
    monkeypatch.setattr(journal_logging, "stderr_is_journal", lambda: journal)
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: captured.update(kw))
    journal_logging.configure_logging()
    (handler,) = captured["handlers"]
    assert type(handler.formatter) is expected
    assert captured["level"] == logging.INFO


INFRA = Path(__file__).resolve().parents[3] / "infra"
SRC_PATH = "Environment=PYTHONPATH=/home/exedev/address-validator/src"


def test_every_unit_running_a_journal_logging_script_has_src_on_path():
    """journal_logging imports address_validator.core.journal (#252). A unit
    without ``src`` on PYTHONPATH fails at import, before it can log anything —
    for unit-failure@, that is the notifier dispatch."""
    users = {p.name for p in INFRA.glob("*.py") if "from journal_logging" in p.read_text()}
    assert "notify_unit_failure.py" in users
    checked = set()
    for unit in INFRA.glob("*.service"):
        text = unit.read_text()
        runs = {name for name in users if f"/infra/{name}" in text}
        if runs:
            assert SRC_PATH in text.splitlines(), unit.name
            checked |= runs
    assert checked == users, f"no unit found for {users - checked}"
