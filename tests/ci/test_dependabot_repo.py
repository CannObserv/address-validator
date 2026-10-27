"""What tests/ci/test_dependabot.py (the skill's asset) doesn't cover here (#361).

- Every Python requirement keeps DEPENDENCY-POLICY's major bound, so a
  Dependabot ``increase-if-necessary`` bump inside it is a lock change and a
  major is its own PR.
- Ruff has one pin. Dependabot moves one manifest per PR, so a second pin in
  ``.pre-commit-config.yaml`` would drift from ``uv.lock`` on every dev bump.

Pure file reads: no network.
"""

import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_PRE_COMMIT = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))

_REQUIREMENTS = [
    *_PYPROJECT["project"]["dependencies"],
    *(
        spec
        for group in _PYPROJECT.get("dependency-groups", {}).values()
        for spec in group
        if isinstance(spec, str)
    ),
]


@pytest.mark.parametrize("spec", _REQUIREMENTS)
def test_every_requirement_has_a_major_bound(spec: str) -> None:
    """DEPENDENCY-POLICY: ``>=X.Y,<X+1``, no unbounded upper pins."""
    operators = {s.operator for s in Requirement(spec).specifier}
    assert ">=" in operators, f"{spec}: no floor"
    assert "<" in operators, f"{spec}: no upper bound; add <X+1 (docs/DEPENDENCY-POLICY.md)"


def _hooks() -> list[dict]:
    return [hook for repo in _PRE_COMMIT["repos"] for hook in repo.get("hooks", [])]


def test_ruff_is_not_pinned_by_pre_commit() -> None:
    remote = [r["repo"] for r in _PRE_COMMIT["repos"] if "ruff" in r["repo"]]
    assert not remote, (
        f"{remote}: a second ruff pin beside uv.lock drifts on every Dependabot dev bump. "
        "Run ruff through a `repo: local` hook (`uv run --frozen ruff …`)"
    )


@pytest.mark.parametrize("hook_id", ["ruff", "ruff-format"])
def test_ruff_hooks_run_the_locked_ruff(hook_id: str) -> None:
    hooks = [h for h in _hooks() if h["id"] == hook_id]
    assert len(hooks) == 1, f"expected one {hook_id} hook, found {len(hooks)}"
    entry = hooks[0].get("entry", "")
    assert entry.startswith("uv run --frozen ruff "), (
        f"{hook_id} runs {entry!r}, not the ruff uv.lock pins"
    )
