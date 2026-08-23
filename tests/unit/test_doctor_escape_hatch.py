"""`.skills/doctor.sh` must stay a real file, never a symlink (GH #203).

A fresh worktree checks out the repo's committed symlinks but not its
submodules, so every ``skills/``, ``.claude/skills/`` and ``.claude/hooks/``
entry — all of which point into ``skills-vendor/`` — dangles until something
initializes them. ``.skills/doctor.sh`` is what initializes them, which is why
it is *installed* as a real file rather than symlinked into the vendor tree
like everything else.

That is a load-bearing exception, and an easy one to "tidy up" into a symlink
for consistency with its neighbours. If that happens the escape hatch dangles
in exactly the situation it exists to repair: a fresh worktree would have no
skills, no hooks, and no working doctor to fix either. This test is the guard.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR = REPO_ROOT / ".skills" / "doctor.sh"


def test_doctor_is_a_real_file_not_a_symlink() -> None:
    assert DOCTOR.exists(), f"missing {DOCTOR} — the worktree escape hatch (GH #203)"
    assert not DOCTOR.is_symlink(), (
        f"{DOCTOR} is a symlink.\n\n"
        "It must be a real file. Every other skill entry symlinks into "
        "skills-vendor/, which is exactly why this one cannot: in a fresh "
        "worktree the submodules are not initialized, so a symlinked doctor "
        "would dangle in the one situation it exists to repair.\n"
        "Reinstall it with managing-skills' install-doctor.sh.\n"
        "See docs/DEPLOYMENT.md -> 'Why `bash .skills/doctor.sh` is the other half'."
    )
    assert DOCTOR.is_file(), f"{DOCTOR} exists but is not a regular file"
    assert os.access(DOCTOR, os.X_OK), (
        f"{DOCTOR} is not executable — the skill preflights gate on "
        "`[ -x .skills/doctor.sh ]` and will silently skip it."
    )
