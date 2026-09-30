"""Drift guard: every timer-driven and restarting unit reports its failure (GH #228, #248).

audit-archive and docker-prune failed every night for months after #109 and
nobody noticed: a failed oneshot only sets ``failed`` state, which nothing
reads. Each timer's service therefore carries
``OnFailure=unit-failure@%n.service``, and that template logs one line tagged
``unit-failure`` at ``crit`` priority (``journalctl -t unit-failure``).

Guarded here:

- a new ``infra/*.timer`` whose service lacks the hook — the failure is silent again;
- a restarting (``Restart=``) service without the hook, or with it but without
  ``RestartMode=direct`` and a reachable start limit (#248). Under the default
  ``RestartMode=normal`` every crash passes through ``failed`` and fires the hook
  with a fresh invocation ID, so a crashloop sends one dispatch per restart.
  ``direct`` fires it once, when the start limit trips — and only if the limit
  can trip: at the defaults (10s / 5) a ``RestartSec=3`` loop never reaches it;
- the handler losing its tag/priority, so ``journalctl -t unit-failure`` finds nothing;
- the notifier dispatch (#232) losing its ``-`` prefix, or displacing the journal line;
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


def _settings(path: Path, name: str) -> dict[str, str]:
    """Return one section's ``Key=value`` settings; the last assignment wins."""
    return dict(line.split("=", 1) for line in _section(path, name) if "=" in line)


def _restarts(service: Path) -> bool:
    return _settings(service, "Service").get("Restart", "no") != "no"


RESTARTING_SERVICES = sorted(s for s in INFRA.glob("*.service") if _restarts(s))


def test_restarting_services_exist() -> None:
    """Guard against the filter silently matching nothing."""
    names = {s.name for s in RESTARTING_SERVICES}
    assert {"address-validator.service", "libpostal.service"} <= names


@pytest.mark.parametrize("service", RESTARTING_SERVICES, ids=lambda p: p.name)
def test_restarting_service_reports_failure_once(service: Path) -> None:
    unit = _settings(service, "Unit")
    svc = _settings(service, "Service")
    assert HOOK in _section(service, "Unit"), f"{service.name} lacks {HOOK}"
    assert svc.get("RestartMode") == "direct", (
        f"{service.name}: without RestartMode=direct every restart fires the hook"
    )
    for key in ("StartLimitIntervalSec", "StartLimitBurst"):
        assert key in unit, f"{service.name}: {key} must be explicit in [Unit]"


@pytest.mark.parametrize("service", RESTARTING_SERVICES, ids=lambda p: p.name)
def test_restarting_service_start_limit_is_reachable(service: Path) -> None:
    """A crashloop must fit ``StartLimitBurst`` starts inside the interval, or it never fails.

    Each cycle takes at least ``RestartSec``, so ``burst * RestartSec`` is the
    fastest a loop can spend its burst. Plain seconds only, so this stays exact.
    """
    unit = _settings(service, "Unit")
    interval = int(unit["StartLimitIntervalSec"])
    burst = int(unit["StartLimitBurst"])
    restart_sec = int(_settings(service, "Service").get("RestartSec", "0"))
    assert interval > 0, f"{service.name}: StartLimitIntervalSec=0 disables the limit"
    assert burst * restart_sec < interval, (
        f"{service.name}: {burst} starts x RestartSec={restart_sec} >= {interval}s — "
        "a crashloop never reaches the limit"
    )


def _exec_starts() -> list[str]:
    return [line for line in _section(HANDLER, "Service") if line.startswith("ExecStart=")]


def test_handler_logs_tagged_crit_line_first() -> None:
    """The journal line is the fallback: it runs before, and apart from, the dispatch."""
    assert "Type=oneshot" in _section(HANDLER, "Service")
    first = _exec_starts()[0]
    assert "/usr/bin/logger -t unit-failure -p daemon.crit" in first
    assert "%i" in first


def test_handler_notifier_dispatch_is_fail_open() -> None:
    """#232: the dispatch is a second ExecStart= whose failure systemd ignores (`-`)."""
    exec_start = _exec_starts()
    assert len(exec_start) == 2
    dispatch = exec_start[1].removeprefix("ExecStart=")
    assert dispatch.startswith("-"), "a failed dispatch must not fail the handler"
    assert "infra/notify_unit_failure.py %i" in dispatch


def test_handler_does_not_hook_itself() -> None:
    assert not any(line.startswith("OnFailure=") for line in _section(HANDLER, "Unit"))
