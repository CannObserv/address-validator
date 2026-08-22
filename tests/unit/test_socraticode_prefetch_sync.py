"""Drift guard: the prefetch string in the docs must equal the hook's (GH #199).

The `codebase_*` MCP tools are deferred — an agent can only call one after a
`ToolSearch` prefetch loads its schema. Two places carry that `select:` string:
`.claude/hooks/socraticode-reminder.sh` (a symlink into the vendored skills
submodule, which prints it at SessionStart) and `docs/SOCRATICODE.md`, which
tells agents to run it verbatim when the hook did not fire.

They drifted once already: a vendored-skills bump moved the hook from a 9-tool
subset to the 12-tool set the docs had documented ahead of it, and nothing
noticed either side of that gap. A doc that names fewer tools than the hook
sends agents to `grep` for questions the graph tools answer; a doc that names
more sends them to `InputValidationError`. Both fail silently.

Reads the hook through its symlink deliberately — the vendored script is what
actually runs, so a submodule bump that rewords the string fails here.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "socraticode-reminder.sh"
DOC = REPO_ROOT / "docs" / "SOCRATICODE.md"

SELECT_RE = re.compile(r"select:mcp__[\w,]+")


def _select_strings(path: Path) -> list[str]:
    return SELECT_RE.findall(path.read_text(encoding="utf-8"))


def test_hook_and_doc_are_present() -> None:
    assert HOOK.is_file(), f"missing (dangling symlink?): {HOOK}"
    assert DOC.is_file(), f"missing: {DOC}"


def test_doc_declares_exactly_one_prefetch_string() -> None:
    found = _select_strings(DOC)
    assert len(found) == 1, (
        f"expected exactly one select: string in {DOC.name}, found {len(found)} — "
        "a second copy is a drift site"
    )


def test_doc_prefetch_matches_the_hook() -> None:
    hook_strings = _select_strings(HOOK)
    assert hook_strings, f"no select: string found in {HOOK} — did the hook change shape?"
    doc_string = _select_strings(DOC)[0]
    assert doc_string in hook_strings, (
        "prefetch drift between the SessionStart hook and docs/SOCRATICODE.md.\n"
        f"  hook: {hook_strings[0]}\n"
        f"  doc:  {doc_string}\n"
        "Copy the hook's string into the doc (the hook is what actually runs)."
    )
