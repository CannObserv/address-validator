"""Guards for the session skill listing (docs/SKILLS.md → Listing overrides).

- Every ``skillOverrides`` key in ``.claude/settings.json`` names a skill under
  ``.claude/skills/``: a renamed skill would otherwise drop back into the
  listing at full cost, and the stale key would hide nothing.
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

# skill -> the skill that invokes it via the Skill tool.
DELEGATED_TO = {"enforcing-architecture": "reviewing-architecture"}


def _overrides() -> dict[str, str]:
    return json.loads(SETTINGS.read_text()).get("skillOverrides", {})


def test_override_keys_name_existing_skills():
    known = {p.name for p in CLAUDE_SKILLS.iterdir()}
    stale = sorted(set(_overrides()) - known)
    assert not stale, f"skillOverrides keys with no .claude/skills/ entry: {stale}"


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
