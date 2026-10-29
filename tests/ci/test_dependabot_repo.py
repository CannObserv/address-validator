"""What tests/ci/test_dependabot.py (the skill's asset) doesn't cover here (#361).

- Every Python requirement keeps DEPENDENCY-POLICY's major bound, so a
  Dependabot ``increase-if-necessary`` bump inside it is a lock change and a
  major is its own PR.
- Ruff has one pin. Dependabot moves one manifest per PR, so a second pin in
  ``.pre-commit-config.yaml`` would drift from ``uv.lock`` on every dev bump.
- npm and pre-commit PR limits outlast a full batch, derived as the asset
  derives uv's and github-actions'. Past the limit no version update opens.
- The npm and pre-commit blocks scan the directory each manifest is in.
- Runtime and npm groups take minor and patch only: each major is its own PR,
  a decision against the major bound and a changelog read.

Pure file reads: no network.
"""

import json
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_PRE_COMMIT = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
_PACKAGE_JSON = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"

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


@pytest.mark.parametrize("spec", _REQUIREMENTS)
def test_upper_bound_is_the_floors_next_major(spec: str) -> None:
    """``>=X.Y,<X+1`` exactly: one major per range (#384).

    Dependabot's ``increase-if-necessary`` widens a major bump to ``>=old,<new+1``
    (#373 wrote ``python-json-logger>=3.3,<5``), which the presence check above
    passes.
    """
    bounds = {s.operator: Version(s.version) for s in Requirement(spec).specifier}
    floor, upper = bounds[">="], bounds["<"]
    assert upper == Version(str(floor.major + 1)), (
        f"{spec}: the range spans more than one major; raise the floor to the new "
        f"major (>={upper.major - 1}.x,<{upper.major}), per docs/DEPENDENCY-POLICY.md"
    )


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


def test_ruff_format_hook_covers_markdown() -> None:
    """ruff 0.17 formats Python blocks in Markdown and pre-ship checks `.` (#377)."""
    (hook,) = [h for h in _hooks() if h["id"] == "ruff-format"]
    assert "markdown" in hook.get("types_or", []), (
        "ruff-format skips .md: a doc edit passes the commit hook, then fails pre-ship's "
        "`ruff format --check .`"
    )


def _blocks(ecosystem: str) -> list[dict]:
    updates = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")).get("updates") or []
    blocks = [u for u in updates if u.get("package-ecosystem") == ecosystem]
    assert blocks, f"no {ecosystem} block in .github/dependabot.yml"
    return blocks


def _remote_hook_repos() -> list[str]:
    return [r["repo"] for r in _PRE_COMMIT["repos"] if r["repo"] not in {"local", "meta"}]


@pytest.mark.parametrize(
    ("ecosystem", "batch"),
    [
        # Each package can be a major, its own PR; groups add one each.
        (
            "npm",
            lambda b: (
                len(_PACKAGE_JSON.get("devDependencies", {}))
                + len(_PACKAGE_JSON.get("dependencies", {}))
                + len(b.get("groups") or {})
            ),
        ),
        # One PR per remote hook repo.
        ("pre-commit", lambda b: len(_remote_hook_repos())),
    ],
    ids=["npm", "pre-commit"],
)
def test_open_pr_limit_outlasts_a_full_batch(ecosystem: str, batch) -> None:
    for block in _blocks(ecosystem):
        limit = block.get("open-pull-requests-limit", 5)
        assert limit > batch(block), (
            f"{ecosystem}: limit {limit} can be filled by {batch(block)} PRs; past it "
            "Dependabot opens no more version updates and says so nowhere"
        )


def test_the_hook_repo_count_sees_remote_repos() -> None:
    """Guard the guard: shellcheck-py is remote, so the pre-commit batch isn't 0."""
    assert _remote_hook_repos(), "no remote hook repo found: the derivation broke"


@pytest.mark.parametrize("ecosystem", ["uv", "npm"])
def test_grouped_bumps_leave_majors_to_their_own_prs(ecosystem: str) -> None:
    for block in _blocks(ecosystem):
        for name, group in (block.get("groups") or {}).items():
            if group.get("dependency-type") == "development" and ecosystem == "uv":
                continue  # dev-tools takes every update type: no restart, no prod risk
            types = set(group.get("update-types") or ["major", "minor", "patch"])
            assert types <= {"minor", "patch"}, (
                f"{ecosystem} group {name!r} takes {sorted(types)}: a major bump belongs "
                "in its own PR"
            )


@pytest.mark.parametrize(
    ("ecosystem", "manifest"), [("npm", "package.json"), ("pre-commit", ".pre-commit-config.yaml")]
)
def test_block_covers_every_manifest(ecosystem: str, manifest: str) -> None:
    """A wrong directory scans nothing and opens nothing."""
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    parents = {
        PurePosixPath(f).parent.as_posix() for f in tracked if PurePosixPath(f).name == manifest
    }
    expected = {"/" if d == "." else f"/{d}" for d in parents}
    assert expected, f"no tracked {manifest}: the derivation broke"
    covered = {
        d
        for b in _blocks(ecosystem)
        for d in [*b.get("directories", []), *([b["directory"]] if "directory" in b else [])]
    }
    assert expected <= covered, f"{ecosystem} manifests not scanned: {sorted(expected - covered)}"
