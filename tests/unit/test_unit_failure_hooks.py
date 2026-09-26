"""Drift guard: every timer-driven unit reports its failure to the journal (GH #228).

audit-archive and docker-prune failed every night for months after #109 and
nobody noticed: a failed oneshot only sets ``failed`` state, which nothing
reads. Each timer's service therefore carries
``OnFailure=unit-failure@%n.service``, and that template logs one line tagged
``unit-failure`` at ``crit`` priority (``journalctl -t unit-failure``).

Guarded here:

- a new ``infra/*.timer`` whose service lacks the hook — the failure is silent again;
- the handler losing its tag/priority, so ``journalctl -t unit-failure`` finds nothing;
- the handler gaining its own ``OnFailure=`` — a failing handler would recurse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"
HOOK = "OnFailure=unit-failure@%n.service"
HANDLER = INFRA / "unit-failure@.service"


def _section(path: Path, name: str) -> list[str]:
    """Return the stripped, non-comment lines of one ``[name]`` section."""
    lines: list[str] = []
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == name and line and not line.startswith("#"):
            lines.append(line)
    return lines


TIMER_SERVICES = sorted(t.with_suffix(".service") for t in INFRA.glob("*.timer"))


def test_timers_exist() -> None:
    """Guard against the glob silently matching nothing."""
    assert len(TIMER_SERVICES) >= 4


@pytest.mark.parametrize("service", TIMER_SERVICES, ids=lambda p: p.name)
def test_timer_service_reports_failure(service: Path) -> None:
    assert service.exists(), f"{service.name} has a timer but no service"
    assert HOOK in _section(service, "Unit"), f"{service.name} lacks {HOOK}"


def test_handler_logs_tagged_crit_line() -> None:
    service = _section(HANDLER, "Service")
    assert "Type=oneshot" in service
    exec_start = [line for line in service if line.startswith("ExecStart=")]
    assert len(exec_start) == 1
    assert "/usr/bin/logger -t unit-failure -p daemon.crit" in exec_start[0]
    assert "%i" in exec_start[0]


def test_handler_does_not_hook_itself() -> None:
    assert not any(line.startswith("OnFailure=") for line in _section(HANDLER, "Unit"))
