"""One HTTP library in the process that runs: httpx2, never httpx (#420).

notifier-client 0.4.0 sends through httpx2 and re-raises httpx2's exceptions.
An ``except httpx.HTTPError`` around an SDK call stops matching, and nothing
fails to say so: an unreachable notifier is reported as an unexpected failure
instead of an unreachable one. So the whole process moved to httpx2, as
co-status did (CannObserv/status#125), and these checks keep the split from
coming back.

Two checks, because each misses what the other catches:

- an AST walk over everything that runs against production, so a stray
  ``import httpx`` in a module no test imports still fails;
- a fresh interpreter importing every ``src`` and ``infra`` module, so a
  *dependency* that imports httpx (which would put it back in ``uv.lock`` and
  split the stack again) fails too.

Unlike co-status, this suite runs no ``httpx2.alias_httpx()``: there is no
respx here, so nothing needs httpx at all.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: What runs against production: the service, its migrations, its ops scripts.
AREAS = ("src", "infra", "scripts", "alembic")
FORBIDDEN = {"httpx", "httpcore"}


def _imports(path: Path) -> set[str]:
    """Top-level modules one file imports, anywhere in its body."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _sources() -> list[Path]:
    return sorted(path for area in AREAS for path in (REPO_ROOT / area).rglob("*.py"))


def test_sources_were_found():
    """A walk over nothing would pass every check below."""
    names = {path.name for path in _sources()}
    assert {"registry.py", "libpostal_client.py", "notify_unit_failure.py"} <= names


@pytest.mark.parametrize("path", _sources(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_nothing_that_runs_imports_httpx(path):
    found = _imports(path) & FORBIDDEN
    assert not found, (
        f"{path.relative_to(REPO_ROOT)} imports {', '.join(sorted(found))}. The notifier "
        f"SDK raises httpx2's exceptions; import httpx2 (and httpcore2) instead (#420)."
    )


#: Imports every module under src/ and infra/, then reports which of
#: httpx/httpcore loaded. infra's entry points are ``__main__``-guarded.
_PROBE = """
import importlib, json, pkgutil, sys
from pathlib import Path
import address_validator
for module in pkgutil.walk_packages(address_validator.__path__, "address_validator."):
    importlib.import_module(module.name)
for path in sorted(Path("infra").glob("*.py")):
    importlib.import_module(path.stem)
loaded = sorted(m for m in sys.modules if m.split(".")[0] in {"httpx", "httpcore"})
print(json.dumps({"loaded": loaded, "sdk": "notifier_client" in sys.modules}))
"""


def test_the_service_loads_no_httpx_at_run_time():
    """Transitively too: a dependency importing httpx would split the stack again."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", "infra"])}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["sdk"], "the probe never imported notifier_client"
    assert report["loaded"] == []
