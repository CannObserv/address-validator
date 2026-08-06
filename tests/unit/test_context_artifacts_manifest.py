"""Drift guard for the SocratiCode context-artifact manifest (GH #188).

``.socraticodecontextartifacts.json`` declares the non-code knowledge sources
the SocratiCode server indexes for `codebase_context*` search. A well-formed but
non-resolving ``path`` is **skipped silently** — the server reports a short
artifact count, not an error — so the manifest drifted for a full spec bump
before anyone noticed the USPS API contract had stopped being searchable.

This test is the cheap, dependency-free half of the upstream
``mcp-driver.mjs validate-manifest`` check: every declared path must resolve.
If you vendor a new revision of a spec or move a doc, repoint its entry here.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / ".socraticodecontextartifacts.json"


def _artifacts() -> list[dict]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return payload["artifacts"]


def test_manifest_present_and_well_formed() -> None:
    assert MANIFEST.is_file(), f"missing manifest: {MANIFEST}"
    artifacts = _artifacts()
    assert artifacts, "manifest declares no artifacts"
    for artifact in artifacts:
        assert artifact.get("name"), f"artifact without a name: {artifact}"
        assert artifact.get("path"), f"artifact without a path: {artifact}"


def test_every_artifact_path_resolves() -> None:
    missing = [
        f"{a['name']} -> {a['path']}" for a in _artifacts() if not (REPO_ROOT / a["path"]).exists()
    ]
    assert not missing, (
        "context artifacts point at paths that do not exist — the SocratiCode "
        "server skips these silently:\n  " + "\n  ".join(missing)
    )


def test_artifact_names_are_unique() -> None:
    names = [a["name"] for a in _artifacts()]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"duplicate artifact names: {duplicates}"
