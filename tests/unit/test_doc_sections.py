"""Drift guard for ``.skills/doc-sections`` (GH #210).

``doc-check.sh`` (shipping-work Step 1.5) prints this file's lines as the doc
sections to spot-check whenever a ``.skills/doc-sensitive-paths`` entry hits.
Upstream deliberately checks nothing about it — the lines are prose
(gregoryfoster/skills#284) — so a renamed doc or heading would leave the
advice pointing at nothing, silently, on every hit.

This test pins the part of the prose that *is* checkable, by convention of the
file's own format: each line opens with one tracked doc path before its colon,
and every ``"quoted"`` phrase on the line is a heading in that doc. It also
holds the file in step with the path list it routes: every path-list entry is
named on some line, so no hit prints only lines about other files.

Upstream declines that last check (gregoryfoster/skills#284) because pasting
paths into prose satisfies it while making the advice worse. Here the pasted
paths *are* the advice: the parenthetical on each line is the key a reader
matches a hit against.
"""

import re
import subprocess
from pathlib import Path

from tests.unit.test_doc_sensitive_paths import _entries as _path_list_entries

REPO_ROOT = Path(__file__).resolve().parents[2]
SECTIONS = REPO_ROOT / ".skills" / "doc-sections"

# Path-list entries no doc inventories, so no line can route them. Each needs
# its reason in .skills/doc-sections' header.
_UNROUTED = {".github/workflows/"}

# An ATX closing run must follow whitespace (CommonMark), so ``## Use C#`` keeps its ``#``.
_HEADING = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?\s*$")
_QUOTED = re.compile(r'"([^"]+)"')


def _lines() -> list[str]:
    """Parse the file the way ``doc-check.sh``'s ``read_list_file`` does: blank
    lines and whole-line ``#``-comments dropped, surrounding whitespace
    trimmed."""
    lines = []
    for line in SECTIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lines.append(line.strip())
    return lines


def _split(line: str) -> tuple[str, str]:
    """``<doc>: <advice>`` → ``(doc, advice)``; ``doc`` is ``""`` without a colon."""
    doc, colon, advice = line.partition(":")
    return (doc.strip(), advice) if colon else ("", line)


def _tracked_files() -> set[str]:
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.splitlines())


def _headings(doc: Path) -> set[str]:
    """Markdown ATX headings, skipping fenced code blocks — a ``# comment``
    inside a shell fence is not a section anyone can navigate to."""
    headings = set()
    in_fence = False
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and (m := _HEADING.match(line)):
            headings.add(m.group(1))
    return headings


def test_sections_file_is_non_empty() -> None:
    """An empty override makes doc-check.sh exit 2, not fall back to defaults."""
    assert _lines(), f"{SECTIONS} lists no sections"


def test_every_line_names_a_tracked_doc() -> None:
    """The text before the first colon is the doc a hit sends the reader to."""
    tracked = _tracked_files()
    missing = [line for line in _lines() if _split(line)[0] not in tracked]
    assert not missing, (
        "these .skills/doc-sections lines do not open with a tracked doc path "
        f"followed by a colon: {missing}"
    )


def test_every_quoted_heading_exists_in_its_doc() -> None:
    """A quoted phrase names a section of the line's doc; a rename strands it."""
    stale = []
    for line in _lines():
        doc, advice = _split(line)
        path = REPO_ROOT / doc
        if not doc or not path.is_file():
            continue  # reported by test_every_line_names_a_tracked_doc
        headings = _headings(path)
        stale += [f"{doc}: {q!r}" for q in _QUOTED.findall(advice) if q not in headings]
    assert not stale, (
        f"these headings named in .skills/doc-sections do not exist in their doc: {stale}"
    )


def test_every_path_list_entry_is_routed() -> None:
    """Each doc-sensitive-paths entry is either a line's doc or named, as a
    whole token, in some line's advice — ``skills/`` inside ``.skills/`` does
    not count."""
    split = [_split(line) for line in _lines()]
    docs = {doc for doc, _ in split}
    advice = " ".join(a for _, a in split)
    unrouted = [
        entry
        for entry in _path_list_entries()
        if entry not in docs
        and entry not in _UNROUTED
        and not re.search(rf"(?<![\w./-]){re.escape(entry)}(?![\w-])", advice)
    ]
    assert not unrouted, (
        "these .skills/doc-sensitive-paths entries appear on no .skills/doc-sections "
        f"line, so a hit on one prints no advice about it: {unrouted}"
    )


def test_unrouted_exemptions_are_still_path_list_entries() -> None:
    """An exemption for an entry the path list dropped is dead weight."""
    stale = _UNROUTED - set(_path_list_entries())
    assert not stale, f"drop these from _UNROUTED; the path list no longer has them: {stale}"
