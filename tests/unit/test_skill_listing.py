"""Guards for the session skill listing (docs/SKILLS.md → Listing overrides).

- Every ``skillOverrides`` key in ``.claude/settings.json`` names a skill under
  ``.claude/skills/`` or a Claude Code built-in listed in ``BUILTIN``: a renamed
  skill would otherwise drop back into the listing at full cost, and the stale
  key would hide nothing. Built-ins are keyed by skill name, not slash alias
  (``/ship`` is ``ship-local``).
- A skill another skill hands off to through the Skill tool must stay callable
  by Claude, so it may be ``name-only`` but never ``user-invocable-only`` or
  ``off``.
- In-repo SKILL.md frontmatter (real directories under ``skills/``, not
  vendored symlinks) parses as strict YAML. An unquoted ``: `` or `` #`` in a
  plain scalar breaks the parse or silently truncates the value
  (gregoryfoster/skills#709).
"""

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
CLAUDE_SKILLS = REPO_ROOT / ".claude" / "skills"
SKILLS = REPO_ROOT / "skills"

ALLOWED = {"on", "name-only", "user-invocable-only", "off"}

# Claude Code built-in skills this repo overrides (GH #442). Not on disk, so
# listed here; add a name only with the override that uses it.
BUILTIN = {
    "claude-api",
    "code-review",
    "dataviz",
    "fewer-permission-prompts",
    "keybindings-help",
    "run",
    "ship-local",
    "simplify",
    "steward",
    "workflow-authoring",
}

# Built-ins kept fully listed on purpose (GH #442): Claude must trigger them
# from the user's wording ("every 5 minutes", "from now on when X").
KEEP_ON = {"loop", "schedule", "update-config"}

# skill -> what invokes it via the Skill tool, so it must stay model-callable.
DELEGATED_TO = {
    "enforcing-architecture": "reviewing-architecture",
    "workflow-authoring": "the Workflow tool's instructions",
}


def _overrides() -> dict[str, str]:
    return json.loads(SETTINGS.read_text()).get("skillOverrides", {})


def test_override_keys_name_existing_skills():
    known = {p.name for p in CLAUDE_SKILLS.iterdir()} | BUILTIN
    stale = sorted(set(_overrides()) - known)
    assert not stale, f"skillOverrides keys naming no repo skill or BUILTIN: {stale}"


def test_builtin_list_has_no_unused_names():
    unused = sorted(BUILTIN - set(_overrides()))
    assert not unused, f"BUILTIN names with no override: {unused}"


@pytest.mark.parametrize("skill", sorted(KEEP_ON))
def test_kept_builtins_stay_listed(skill):
    assert _overrides().get(skill, "on") == "on", f"{skill} is kept fully listed (GH #442)"


def test_override_values_are_valid():
    bad = {k: v for k, v in _overrides().items() if v not in ALLOWED}
    assert not bad, f"invalid skillOverrides values: {bad}"


@pytest.mark.parametrize("skill", sorted(DELEGATED_TO))
def test_delegated_skills_stay_model_callable(skill):
    mode = _overrides().get(skill, "on")
    assert mode in {"on", "name-only"}, (
        f"{skill} is invoked by {DELEGATED_TO[skill]} via the Skill tool; "
        f"{mode!r} would block that hand-off"
    )


IN_REPO_SKILLS = sorted(
    p for p in SKILLS.iterdir() if p.is_dir() and not p.is_symlink() and (p / "SKILL.md").exists()
)


@pytest.mark.parametrize("skill_dir", IN_REPO_SKILLS, ids=lambda p: p.name)
def test_in_repo_frontmatter_is_strict_yaml(skill_dir):
    text = (skill_dir / "SKILL.md").read_text()
    assert text.startswith("---\n")
    fm = yaml.safe_load(text.split("---", 2)[1])
    assert fm["name"] == skill_dir.name
    desc = fm["description"]
    assert desc.count('"') % 2 == 0, f"unbalanced quote — truncated description? {desc[-60:]!r}"
