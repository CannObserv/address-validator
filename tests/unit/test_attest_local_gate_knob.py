"""The local-gate attestation knob reads ``on`` and stays committed (#427).

``.skills/attest-local-gate`` tells the vendored ``shipping-work`` ``push.sh``
to gate the exact clean tree before it pushes, then post the commit status
``gate/local`` = ``success`` on the pushed SHA (gregoryfoster/skills#688).
There is no PR CI here, so the status is evidence on each PR head, not a skip.

The knob is read from **the commit being shipped**, never the work tree, and
only a regular file counts: absent or ``off`` silently stops attesting, and
anything else (a symlink, two words, an empty file) is exit 2 before the push.
Neither shows up until a ship, so this test is the notice.

The vendored reader runs here against a scratch commit of this tree's knob.
It ships in a submodule CI doesn't initialise: off this host that is a skip, on
it a failure (``bash .skills/doctor.sh``). The rest are pure parses.

Shape from ``tests/unit/test_merge_mode_knob.py``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOB_REL = ".skills/attest-local-gate"
KNOB = REPO_ROOT / KNOB_REL
ATTEST = REPO_ROOT / "skills" / "shipping-work" / "scripts" / "_attest-gate.sh"
HOST = "address-validator"

# git exports GIT_DIR to every hook it runs, and it beats ``-C``: a suite run
# from a hook would otherwise ``git init`` into, and ask, the wrong repository.
GIT_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"}
}


def _git(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
        env=GIT_ENV,
    )


def _require_checkout() -> None:
    if shutil.which("git") is None:
        pytest.skip("no git")
    if _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout (a source export): nothing to be tracked in")


def _require_vendored() -> None:
    if not ATTEST.is_file():
        if socket.gethostname() == HOST:
            pytest.fail(f"{ATTEST} missing on the host: run bash .skills/doctor.sh")
        pytest.skip("skills submodule not initialised")


def _words() -> list[str]:
    """Every word outside whole-line ``#`` comments, by _attest-gate.sh's grammar."""
    words: list[str] = []
    for line in KNOB.read_text().splitlines():
        parts = line.split()
        if parts and not parts[0].startswith("#"):
            words.extend(parts)
    return words


def test_the_knob_says_on() -> None:
    assert _words() == ["on"]


def test_the_knob_is_a_regular_file() -> None:
    """A symlinked knob is exit 2 at the push, never ``on``."""
    assert KNOB.is_file()
    assert not KNOB.is_symlink()


def test_the_knob_is_committed_not_ignored() -> None:
    """Only a committed knob attests: one that is only in the work tree is
    exit 2 at the push, and an ignored one never reaches the commit."""
    _require_checkout()
    tracked = _git("ls-files", "--error-unmatch", KNOB_REL)
    assert tracked.returncode == 0, tracked.stderr
    # push.sh reads the mode from the commit, not the work tree: a knob staged
    # as a symlink (120000) is exit 2 at the push whatever the disk shows.
    staged = _git("ls-files", "-s", "--", KNOB_REL)
    assert staged.stdout.split()[:1] in (["100644"], ["100755"]), staged.stdout
    ignored = _git("check-ignore", "-q", "--no-index", KNOB_REL)
    assert ignored.returncode == 1, f"{KNOB_REL} matches a .gitignore pattern"


def test_the_vendored_attestation_script_is_present() -> None:
    """``push.sh`` looks for ``_attest-gate.sh`` beside itself; without it a
    ship with the knob on is exit 2, nothing pushed."""
    _require_vendored()
    assert ATTEST.is_file()


def test_the_vendored_reader_reads_on(tmp_path: Path) -> None:
    """``attest_read_knob`` returns 0 for ``on``, 3 for ``off``, 2 unusable."""
    _require_vendored()
    if shutil.which("git") is None:
        pytest.skip("no git")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=GIT_ENV)
    (tmp_path / ".skills").mkdir()
    shutil.copy(KNOB, tmp_path / KNOB_REL)
    assert _git("add", KNOB_REL, cwd=tmp_path).returncode == 0
    commit = _git(
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "knob",
        cwd=tmp_path,
    )
    assert commit.returncode == 0, commit.stderr
    out = subprocess.run(
        ["bash", "-c", '. "$1" && attest_read_knob', "_", str(ATTEST)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=GIT_ENV,
    )
    assert out.returncode == 0, out.stderr
