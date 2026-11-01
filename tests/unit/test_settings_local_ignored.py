"""Pins `.claude/settings.local.json` ignored and untracked (GH #381 step 1).

The file will hold this host's ``QDRANT_API_KEY`` — the cohort's single global
key for the shared ``co-index`` store, so a leak from any repo means rotating
it on every host at once. This repo is public. The key installer
(CannObserv/index ``scripts/install_qdrant_key.sh``) refuses any target git
would commit, so this rule must land before the key does.

Mirrors CannObserv/notifier ``tests/deploy/test_socraticode_config.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The file holding this host's QDRANT_API_KEY. Never tracked, always ignored.
SETTINGS_LOCAL = ".claude/settings.local.json"

#: A path that is tracked and not ignored — the control for the probes below.
#: The tracked probe reads any non-zero exit (git's 128 included) as "safe";
#: the ignore probe reads only 0 as "safe", so git failing turns it red.
TRACKED_CONTROL = "AGENTS.md"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def test_settings_local_is_not_tracked():
    """Tracked beats ignored: a file already in the index is committed regardless.

    ``git check-ignore`` alone can't diagnose this — with ``--no-index`` it
    reports the rule even for a path ``git add -f`` already staged
    (CannObserv/archiver@cf97c97). Separate test, separate remedy: rotation.
    """
    tracked = _git("ls-files", "--error-unmatch", SETTINGS_LOCAL).returncode == 0
    assert not tracked, (
        f"{SETTINGS_LOCAL} is TRACKED by git — it holds this host's QDRANT_API_KEY, "
        f"the cohort's single global key. Rotate it, then: git rm --cached {SETTINGS_LOCAL}"
    )


def test_settings_local_is_git_ignored():
    """Asks git rather than reading .gitignore — the rule's source doesn't matter."""
    ignored = _git("check-ignore", "-q", SETTINGS_LOCAL).returncode == 0
    assert ignored, f"{SETTINGS_LOCAL} is not git-ignored: it would be committed"


def test_the_probes_discriminate():
    """Prove each probe can return the answer it guards against.

    Tracked: a wrong cwd or a git exiting 128 reads as "not tracked" — green
    while checking nothing — so a known-tracked path must answer tracked.
    Ignored: a rule broad enough to ignore everything reads as green too, so a
    known-unignored path must answer not ignored.
    """
    assert _git("ls-files", "--error-unmatch", TRACKED_CONTROL).returncode == 0, (
        f"{TRACKED_CONTROL} is tracked, so the tracked probe is not answering"
    )
    assert _git("check-ignore", "-q", TRACKED_CONTROL).returncode != 0, (
        f"{TRACKED_CONTROL} is not ignored, so the ignore probe is not answering"
    )
