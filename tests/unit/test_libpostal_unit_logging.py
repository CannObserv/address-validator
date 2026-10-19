"""PII guard: the libpostal sidecar's per-request log line never reaches a log (GH #328).

``wof-libpostal-server`` logs ``STATUS parse '<address>' <duration>`` to stdout
on every request, and has no flag to turn it off (upstream ``main`` defines
only ``-host`` and ``-port``). Two copies existed, and each needs its own switch:

- Docker's ``json-file`` log, under ``/var/lib/docker/containers/``, unbounded.
  ``--log-driver=none`` removes it.
- journald, through the attached ``docker run`` CLI. ``--log-driver=none`` does
  **not** remove this one: the CLI still streams the container's stdout
  (measured 2026-10-19 on a throwaway container). ``StandardOutput=null``
  removes it.

stderr stays in the journal: the server's ERROR/FATAL lines go to both streams,
and the docker CLI's own errors (image, port, OCI runtime) go to stderr, so a
crashloop still shows why.
"""

from __future__ import annotations

import re
from pathlib import Path

UNIT = Path(__file__).resolve().parents[2] / "infra" / "libpostal.service"


def _service_settings() -> dict[str, str]:
    """``[Service]`` ``Key=value`` settings, comments dropped; the last one wins."""
    settings: dict[str, str] = {}
    current = None
    for raw in UNIT.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == "Service" and "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            settings[key] = value
    return settings


def _docker_run_args() -> list[str]:
    """The ``ExecStart=`` command's words, continuation lines joined."""
    text = re.sub(r"\\\n\s*", " ", UNIT.read_text())
    (command,) = re.findall(r"^ExecStart=(.*)$", text, re.M)
    return command.split()


def test_the_container_keeps_no_log() -> None:
    args = _docker_run_args()
    assert args[:2] == ["/usr/bin/docker", "run"], args
    assert "--log-driver=none" in args, "the json-file copy is back"
    assert not any(a.startswith("--log-opt") for a in args), "--log-opt is moot under none"


def test_the_attached_stdout_is_discarded() -> None:
    assert _service_settings().get("StandardOutput") == "null", (
        "the attached docker CLI streams every parsed address into journald"
    )


def test_stderr_still_reaches_the_journal() -> None:
    assert _service_settings().get("StandardError") == "journal", (
        "startup and crash errors would vanish with stdout"
    )
