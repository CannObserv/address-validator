"""Drift test for the `.skills/worktree_venv` opt-out (GH #201).

On the single-VM dev+prod box the main checkout is the systemd unit's
``WorkingDirectory=``, so the `link` default of ``worktree-create.sh`` would
hand every worktree a symlink to the venv the port-8000 service runs from. A
``uv sync`` in such a worktree *prunes* packages out from under a live uvicorn
— a ``ModuleNotFoundError`` crashloop a restart does not recover.

The opt-out is a single untracked, gitignored file. Nothing else notices if it
goes missing (a VM rebuild, an over-eager clean), and the failure it re-enables
is silent until production falls over. This test is the notice.

It is deliberately machine-scoped: it asserts only where the hazard exists —
where the primary checkout *is* the unit's ``WorkingDirectory``. Every other
clone and CI gets the `link` default, which is correct there, and skips.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[2] / "infra" / "address-validator.service"
WORKDIR_RE = re.compile(r"^WorkingDirectory=(.+)$", re.MULTILINE)


def _primary_checkout() -> Path:
    """The main checkout, even when this runs from a linked worktree.

    ``--git-common-dir`` points at the primary ``.git`` directory from anywhere
    in the worktree set; its parent is the primary working tree.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    ).stdout.strip()
    return Path(out).resolve().parent


def _unit_working_directory() -> Path | None:
    if not UNIT.is_file():
        return None
    m = WORKDIR_RE.search(UNIT.read_text(encoding="utf-8"))
    return Path(m.group(1).strip()).resolve() if m else None


def test_worktree_venv_is_none_on_the_service_box() -> None:
    workdir = _unit_working_directory()
    if workdir is None:
        pytest.skip(f"no WorkingDirectory= in {UNIT}")

    primary = _primary_checkout()
    if primary != workdir:
        pytest.skip(
            f"not the service box: primary checkout {primary} is not the unit's "
            f"WorkingDirectory {workdir}; the `link` default is correct here"
        )

    knob = primary / ".skills" / "worktree_venv"
    assert knob.is_file(), (
        f"missing {knob}\n\n"
        "This checkout is the address-validator unit's WorkingDirectory, so "
        "worktree-create.sh's `link` default would symlink the production venv "
        "into every worktree, where a `uv sync` prunes it out from under the "
        "live service. Restore it:\n\n"
        "    echo none > .skills/worktree_venv\n\n"
        "See docs/DEPLOYMENT.md -> 'Why `none` and not `link`' (GH #201)."
    )
    assert knob.read_text(encoding="utf-8").strip() == "none", (
        f"{knob} must read 'none' on the service box, not "
        f"{knob.read_text(encoding='utf-8').strip()!r} — see docs/DEPLOYMENT.md (GH #201)."
    )
