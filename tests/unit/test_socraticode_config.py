"""Drift tests for the SocratiCode shared-store client config (GH #381 steps 2-3).

This repo is a client of ``co-index``, the cohort's shared Qdrant + Ollama
(CannObserv/notifier#57). Two committed files make it one:

- ``.socraticode.json`` names the collections ``codebase_address-validator``
  and so on. Without it the project id is sha256(abs_path)[:12], shared by
  every host that checks a repo out at the same path.
- the ``env`` block in ``.claude/settings.json`` points the server at the store.
  Any drift here falls back silently: a wrong URL reads as a network fault, and
  a missing mode starts local containers again on 0.0.0.0
  (gregoryfoster/skills#494).

The namespace guards are notifier's: both variables split or fragment the
cohort's namespace while every health check stays green. Mirrors
CannObserv/notifier ``tests/deploy/test_socraticode_config.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".socraticode.json"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

#: Upstream's own validator: config.js assertValidProjectId rejects anything else.
PROJECT_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

#: What every client of co-index sets (CannObserv/archiver#226 § Traps 3-4).
CLIENT_ENV = {
    "QDRANT_MODE": "external",
    # Full MagicDNS name: the short name isn't in the cert's SAN, and TLS is
    # mandatory once a key is sent.
    "QDRANT_URL": "https://index.taild0fb76.ts.net:6333",
    "OLLAMA_MODE": "external",
    "OLLAMA_URL": "http://index:11434",
    # One model at one dimension per collection, store-wide.
    "EMBEDDING_MODEL": "nomic-embed-text",
    "EMBEDDING_DIMENSIONS": "768",
}

#: Set, and the cohort's collections split in a way nothing reports: the prefix
#: is prepended to the instance-global socraticode_metadata collection as well
#: as the per-project ones.
COLLECTION_PREFIX = "QDRANT_COLLECTION_PREFIX"

#: "true", and a path-hash project id gains a branch suffix — a fresh
#: six-collection set per branch.
BRANCH_AWARE = "SOCRATICODE_BRANCH_AWARE"

#: Everywhere a variable could reach the MCP server's environment on this host.
#: /etc/address-validator/.env is absent on purpose: only systemd loads it.
NAMESPACE_GUARD_FILES = [
    REPO_ROOT / ".env",
    SETTINGS,
    REPO_ROOT / ".claude" / "settings.local.json",
    Path.home() / ".claude" / "settings.json",
]


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG.read_text())


@pytest.fixture(scope="module")
def env() -> dict:
    return json.loads(SETTINGS.read_text()).get("env", {})


def test_config_exists_and_parses():
    """A malformed file is silently ignored by upstream, not reported.

    loadSocratiCodeConfig catches every parse error and returns null, so a typo
    degrades to the path-hash id with no message.
    """
    assert CONFIG.exists(), f"{CONFIG.name} is missing"
    json.loads(CONFIG.read_text())


def test_project_id_is_the_repo_name(config):
    """Cohort convention: the collections read as codebase_address-validator."""
    assert config["projectId"] == "address-validator"


def test_project_id_is_qdrant_safe(config):
    """Upstream throws on anything outside [a-zA-Z0-9_-]+ rather than sanitizing."""
    assert set(config["projectId"]) <= PROJECT_ID_CHARS


def test_env_block_points_at_co_index(env):
    """Each key compared on its own, so a failure names the one that drifted."""
    for key, want in CLIENT_ENV.items():
        assert env.get(key) == want, f"settings.json env.{key}={env.get(key)!r}, want {want!r}"


def test_env_block_has_no_qdrant_host(env):
    """QDRANT_HOST builds a URL on QDRANT_PORT, default 16333 — reads as a network fault."""
    assert "QDRANT_HOST" not in env


def test_api_key_is_not_in_the_tracked_settings(env):
    """The key belongs in git-ignored settings.local.json; this file is public."""
    assert "QDRANT_API_KEY" not in env


@pytest.mark.parametrize(
    "path", NAMESPACE_GUARD_FILES, ids=lambda p: str(p).replace(str(Path.home()), "~")
)
@pytest.mark.parametrize("variable", [COLLECTION_PREFIX, BRANCH_AWARE])
def test_namespace_guards_are_not_set_anywhere(variable: str, path: Path):
    """VM-local where the file is VM-local; skips loudly rather than passing vacuously.

    The membership test is reduced to a bool *before* the assert on purpose:
    .env and .claude/settings.local.json hold secrets, and
    ``assert variable not in path.read_text()`` puts the whole file into the
    assertion expression, which pytest prints in full under -vv
    (CannObserv/notifier#73).
    """
    if not path.exists():
        pytest.skip(f"{path} not present on this machine")
    declares = variable in path.read_text()
    assert not declares, f"{path.name} sets {variable}"


def test_namespace_guard_failure_does_not_disclose_the_file(tmp_path):
    """A failing guard must not print the file it read — measured, not reasoned.

    Run the real guard under -vv against a file carrying a fake secret and grep
    the output for it.
    """
    secret = "SUPERSECRETPASSWORD-381"
    env_file = tmp_path / "fake.env"
    env_file.write_text(f"API_KEY={secret}\n{COLLECTION_PREFIX}=oops\n")
    # Aliased on import: under its own name pytest collects it in the probe
    # module too, re-running every real parametrization against the secret files.
    probe = tmp_path / "test_guard_probe.py"
    probe.write_text(
        "from pathlib import Path\n\n"
        "from tests.unit.test_socraticode_config import (\n"
        "    COLLECTION_PREFIX,\n"
        "    test_namespace_guards_are_not_set_anywhere as guard,\n"
        ")\n\n\n"
        "def test_probe():\n"
        f"    guard(COLLECTION_PREFIX, Path({str(env_file)!r}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-vv", "--no-cov", "-p", "no:cacheprovider", str(probe)],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=180,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, "probe was expected to fail; the guard did not fire"
    assert f"fake.env sets {COLLECTION_PREFIX}" in output, (
        "probe failed for some reason other than the guard; this test proves nothing"
    )
    disclosed = secret in output
    assert not disclosed, "the guard's failure output contains the file's contents"
