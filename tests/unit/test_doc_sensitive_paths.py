"""Drift guard for ``.skills/doc-sensitive-paths`` (GH #208).

``doc-check.sh`` (shipping-work Step 1.5) reads this file instead of its
built-in defaults, and matches each entry against whole path *segments* at any
depth. An entry that matches no tracked file cannot contribute to the gate's
verdict: upstream only hard-fails when *every* entry is dead, so a single stale
entry degrades to a note printed above a green — the exact
passes-without-being-able-to-fail shape that gregoryfoster/skills#252 fixed.

This test makes the file's central claim enforced rather than asserted: every
entry must match at least one tracked file. If a rename or layout change
strands an entry, fix or drop it here.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PATH_LIST = REPO_ROOT / ".skills" / "doc-sensitive-paths"


def _entries() -> list[str]:
    """Parse the list the way ``doc-check.sh`` does: one path per line, blank
    lines and whole-line ``#``-comments dropped, surrounding whitespace
    trimmed. Trailing comments are *not* stripped — mirroring the shell parser,
    so a stray inline comment shows up here as a dead entry rather than being
    silently tolerated."""
    entries = []
    for line in PATH_LIST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entries.append(line.strip())
    return entries


def _tracked_files() -> list[str]:
    """Tracked paths, matching the gate's ``git -c core.quotePath=false ls-files``."""
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def _path_matches(file: str, entry: str) -> bool:
    """Port of ``path_matches`` from ``doc-check.sh``.

    A trailing-slash entry names a directory; a slash-less entry names a file
    *or* a directory. Every continuation form requires a literal ``/`` after
    the entry, which is what keeps ``pyproject.toml`` from also claiming
    ``pyproject.toml.bak``.
    """
    if entry.endswith("/"):
        return file.startswith(entry) or f"/{entry}" in file
    return (
        file == entry
        or file.endswith(f"/{entry}")
        or file.startswith(f"{entry}/")
        or f"/{entry}/" in file
    )


def test_path_list_is_non_empty() -> None:
    """An empty override makes doc-check.sh exit 2, not fall back to defaults."""
    assert _entries(), f"{PATH_LIST} lists no paths"


def test_every_entry_matches_a_tracked_file() -> None:
    """No entry may be dead — a list that cannot hit is not a passing gate."""
    tracked = _tracked_files()
    dead = [e for e in _entries() if not any(_path_matches(f, e) for f in tracked)]
    assert not dead, (
        "these .skills/doc-sensitive-paths entries match no tracked file, so "
        f"they cannot contribute to Step 1.5's verdict: {dead}"
    )
