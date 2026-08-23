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

**A skip is not automatically good news.** The first version of this test
resolved the primary checkout against the wrong directory, computed ``/home``,
and so skipped in the main checkout — reporting green while guarding nothing
(CR round 2, finding 6). ``_looks_like_the_service_box`` exists to make that
failure mode loud: when the unit's ``WorkingDirectory`` is a real checkout on
this disk but our own detection disagrees with it, that is a broken detector,
not a different machine, and it fails instead of skipping.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
UNIT = HERE.parents[1] / "infra" / "address-validator.service"
WORKDIR_RE = re.compile(r"^WorkingDirectory=(.+)$", re.MULTILINE)


def _primary_checkout() -> Path | None:
    """The main checkout, even when this runs from a linked worktree.

    ``--git-common-dir`` points at the primary ``.git`` from anywhere in the
    worktree set, but it returns a path relative to the process's *own* cwd —
    ``../../.git`` from ``tests/unit`` in the main checkout, an absolute path
    from a worktree. Joining it onto the directory we handed the subprocess is
    correct for both; ``Path(out).resolve()`` is correct only for the absolute
    case and silently produced ``/home`` for the other one.

    Returns None when there is no git to ask — a source export or a container
    without git is "some other environment", which is a skip, not an error.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            cwd=HERE,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return (HERE / out).resolve().parent


def _unit_working_directory() -> Path | None:
    if not UNIT.is_file():
        return None
    m = WORKDIR_RE.search(UNIT.read_text(encoding="utf-8"))
    return Path(m.group(1).strip()) if m else None


def _looks_like_the_service_box(workdir: Path) -> bool:
    """Is the unit's WorkingDirectory a real checkout on this disk?

    If it is, this machine *is* the service box and any disagreement with
    ``_primary_checkout()`` means our detection is broken. If it is not — the
    ordinary case for every other clone and for CI — the path is just a string
    from a committed unit file that happens to name someone else's filesystem.
    """
    return (workdir / ".git").exists() and (workdir / ".venv").exists()


def test_worktree_venv_is_none_on_the_service_box() -> None:
    workdir = _unit_working_directory()
    if workdir is None:
        pytest.skip(f"no WorkingDirectory= in {UNIT}")

    primary = _primary_checkout()
    if primary is None:
        pytest.skip("not a git checkout (no git, or not a repository)")

    if primary != workdir.resolve():
        assert not _looks_like_the_service_box(workdir), (
            f"detection is broken, not 'different machine': {workdir} is a real "
            f"checkout with a .venv — so this IS the service box — but "
            f"_primary_checkout() returned {primary}.\n\n"
            "Skipping here would leave the knob unguarded on the one machine "
            "that needs it. Fix the resolution, do not relax this assert.\n"
            "See docs/DEPLOYMENT.md -> 'Why `none` and not `link`' (GH #201)."
        )
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
    actual = knob.read_text(encoding="utf-8").strip()
    assert actual == "none", (
        f"{knob} must read 'none' on the service box, not {actual!r} — "
        "see docs/DEPLOYMENT.md (GH #201)."
    )
