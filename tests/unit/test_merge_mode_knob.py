"""The merge-mode knob reads ``pr`` and stays committed (#426).

``.skills/merge_mode`` tells the vendored ``shipping-work`` and
``using-git-worktrees`` skills, the skills refresh hook and
``install-cadence.sh`` that work lands on ``main`` through a pull request
merged on GitHub (gregoryfoster/skills#342). **Absent reads as ``direct``**,
which silently restores local merges into the main checkout (a deploy) and
direct pushes to ``main``. **Present but unusable is exit 2**: no ship, no
refresh. Neither shows up as an error until a ship goes wrong, so this test is
the notice.

The skills read the knob from the *primary* checkout, so from a linked worktree
the vendored reader answers for the main checkout, not this tree. The reader
runs here against a copy of this tree's knob in a scratch repository. It ships
in a submodule CI doesn't initialise: off this host that is a skip, on it a
failure (``bash .skills/doctor.sh``). The rest are pure parses.

Shape from ``tests/unit/test_patching_knob.py``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOB = REPO_ROOT / ".skills" / "merge_mode"
READERS = [
    REPO_ROOT / "skills" / "shipping-work" / "scripts" / "merge-mode.sh",
    REPO_ROOT / "skills" / "using-git-worktrees" / "scripts" / "merge-mode.sh",
]
HOST = "address-validator"

# git exports GIT_DIR to every hook it runs, and it beats ``-C``: a suite run
# from a hook would otherwise ``git init`` into, and ask, the wrong repository.
GIT_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"}
}


def _words() -> list[str]:
    """Every word outside whole-line ``#`` comments, by merge-mode.sh's grammar."""
    words: list[str] = []
    for line in KNOB.read_text().splitlines():
        parts = line.split()
        if parts and not parts[0].startswith("#"):
            words.extend(parts)
    return words


def test_the_knob_says_pr() -> None:
    assert _words() == ["pr"]


def test_the_knob_is_committed_not_ignored() -> None:
    """An ignored knob exists only in the checkout that wrote it: every other
    clone reads ``direct``, and so does the ``pr``-rendered cadence workflow,
    which reads the committed knob from its Actions checkout at run time."""
    if shutil.which("git") is None:
        pytest.skip("no git")
    in_repo = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
        env=GIT_ENV,
    )
    if in_repo.returncode != 0:
        pytest.skip("not a git checkout (a source export): nothing to be tracked in")
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", ".skills/merge_mode"],
        capture_output=True,
        text=True,
        check=False,
        env=GIT_ENV,
    )
    assert tracked.returncode == 0, tracked.stderr
    ignored = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", ".skills/merge_mode"],
        capture_output=True,
        check=False,
        env=GIT_ENV,
    )
    assert ignored.returncode == 1, ".skills/merge_mode matches a .gitignore pattern"


@pytest.mark.parametrize("reader", READERS, ids=lambda p: p.parts[-3])
def test_the_vendored_reader_reads_pr(reader: Path, tmp_path: Path) -> None:
    if not reader.is_file():
        if socket.gethostname() == HOST:
            pytest.fail(f"{reader} missing on the host: run bash .skills/doctor.sh")
        pytest.skip("skills submodule not initialised")
    if shutil.which("git") is None:
        pytest.skip("no git")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=GIT_ENV)
    (tmp_path / ".skills").mkdir()
    shutil.copy(KNOB, tmp_path / ".skills" / "merge_mode")
    out = subprocess.run(
        ["bash", str(reader)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=GIT_ENV,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout == "pr\n"


CADENCE = REPO_ROOT / ".github" / "workflows" / "context-cadence.yml"


def test_the_cadence_workflow_is_rendered_for_pr() -> None:
    """A ``direct`` render pushes straight to ``main``, which a PR-only ruleset
    refuses and loses the week's row (gregoryfoster/skills#560). The marker is
    the step ``install-cadence.sh --check`` itself looks for; a re-render under
    the wrong mode, or a hand edit, fails here first."""
    workflow = CADENCE.read_text()
    assert "- name: Read the merge mode" in workflow
    assert "PR_BRANCH=context/weekly-measurement" in workflow
    assert "pull-requests: write" in workflow
