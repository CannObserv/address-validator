"""Drift guard: the prefetch string lives only in the hook, never in the docs.

The `codebase_*` MCP tools are deferred — an agent can only call one after a
`ToolSearch` prefetch loads its schema. `.claude/hooks/socraticode-reminder.sh`
(a symlink into the vendored skills submodule) prints that `select:` string at
SessionStart.

`docs/SOCRATICODE.md` used to carry a verbatim copy, and the two drifted: a
vendored-skills bump moved the hook from a 9-tool subset to a 12-tool set and
nothing noticed (GH #199). Since GH #221 the generated doc points at the hook
instead of copying it (gregoryfoster/skills#209, #234), so there is one source
and nothing to drift. This test keeps it that way: a copy reintroduced by hand
is the drift site coming back.

Reads the hook through its symlink deliberately — the vendored script is what
actually runs, so a submodule bump that changes its shape fails here.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "socraticode-reminder.sh"
DOC = REPO_ROOT / "docs" / "SOCRATICODE.md"
HOOK_REF = ".claude/hooks/socraticode-reminder.sh"

SELECT_RE = re.compile(r"select:mcp__[\w,]+")


def _select_strings(path: Path) -> list[str]:
    return SELECT_RE.findall(path.read_text(encoding="utf-8"))


def test_hook_and_doc_are_present() -> None:
    assert HOOK.is_file(), f"missing (dangling symlink?): {HOOK}"
    assert DOC.is_file(), f"missing: {DOC}"


def test_hook_emits_a_prefetch_string() -> None:
    assert _select_strings(HOOK), f"no select: string found in {HOOK} — did the hook change shape?"


def test_doc_points_at_the_hook_instead_of_copying_it() -> None:
    copies = _select_strings(DOC)
    assert not copies, (
        f"{DOC.name} carries {len(copies)} copy(ies) of the prefetch select: string — "
        f"point at {HOOK_REF} instead; a copy drifts silently (GH #199)"
    )
    assert HOOK_REF in DOC.read_text(encoding="utf-8"), (
        f"{DOC.name} no longer names {HOOK_REF} — "
        "agents have no fallback when the hook did not fire"
    )
