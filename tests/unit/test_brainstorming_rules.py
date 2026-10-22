"""`/brainstorming` runs upstream's text; AGENTS.md's rules must keep naming it (GH #334).

``skills/brainstorming`` is a symlink into the vendored obra-superpowers tree —
no fork, so ``.skills/doctor.sh`` has no override to report drift on. Our
preferences live in AGENTS.md → ``## Design workflow (/brainstorming)``, and each
rule quotes the upstream instruction it replaces: a skill's text is read at
invocation, after AGENTS.md, and a rule that names nothing loses to it.

That only holds while the quoted text still exists upstream. The submodule is
bumped unattended (``.claude/hooks/skills-submodule-update.sh``), so a reworded
phrase would leave its rule pointing at nothing. This test is the guard: on a
failure, requote the rule against the new SKILL.md — never reinstate the fork.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_LINK = REPO_ROOT / "skills" / "brainstorming"
VENDOR_SKILL = REPO_ROOT / "skills-vendor" / "obra-superpowers" / "skills" / "brainstorming"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
SECTION = "## Design workflow (/brainstorming)"

# Upstream text each AGENTS.md rule replaces — exact substrings of both files.
QUOTED_UPSTREAM = (
    "Approval covers what you showed them",
    "docs/superpowers/specs/",
    "superpowers:writing-plans and no other skill",
    "Invoke the superpowers:writing-plans skill",
    "the full written design",
)


def _section() -> str:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert SECTION in text, f"AGENTS.md lost its {SECTION!r} section (GH #334)"
    body = text.split(SECTION, 1)[1]
    return body.split("\n## ", 1)[0]


def _vendor_skill_text() -> str:
    skill_md = VENDOR_SKILL / "SKILL.md"
    assert skill_md.is_file(), (
        f"{skill_md} missing — submodule not initialized. Run `bash .skills/doctor.sh`."
    )
    return skill_md.read_text(encoding="utf-8")


def test_skill_is_a_symlink_to_the_vendored_tree() -> None:
    assert SKILL_LINK.is_symlink(), (
        f"{SKILL_LINK} is no longer a symlink. GH #334 replaced the fork with a "
        "symlink plus AGENTS.md rules; requote a rule instead of re-forking."
    )
    assert SKILL_LINK.resolve() == VENDOR_SKILL.resolve()


def test_each_quoted_phrase_is_in_agents_md() -> None:
    section = _section()
    missing = [p for p in QUOTED_UPSTREAM if p not in section]
    assert not missing, f"AGENTS.md {SECTION!r} no longer quotes: {missing}"


def test_each_quoted_phrase_still_exists_upstream() -> None:
    skill = _vendor_skill_text()
    missing = [p for p in QUOTED_UPSTREAM if p not in skill]
    assert not missing, (
        f"Upstream brainstorming SKILL.md no longer contains {missing}. The "
        f"AGENTS.md rule quoting each now replaces nothing: requote it against "
        f"the new text (docs/SKILLS.md → Brainstorming on this VM)."
    )
