"""Drift guard: env files are parsed, never sourced, and prod reads /etc only (GH #388).

- No unit loads the repo ``.env``. It is the developer's file (``GH_TOKEN*``
  PATs, ``ANTHROPIC_API_KEY``); listed after ``/etc``, it also overrode
  production config. Production reads ``/etc/address-validator/.env`` only.
- No current doc or script sources an env file. Sourcing runs the file as
  shell code; the sites use ``load_env`` from the vendored pre-ship.sh header,
  lifted in place, which reads plain ``KEY=value`` lines.

Dated snapshots (``docs/plans``, ``docs/research``) and the CHANGELOG are not
current guidance, so they are not scanned. Installed-copy drift is
``install-units.sh --check``'s job (#228).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra"
RECIPE = (
    "skills-vendor/gregoryfoster-skills/skills/shipping-work-python-fastapi/scripts/pre-ship.sh"
)
SOURCED_ENV = re.compile(r"(?:\bsource|(?:^|[;&|]\s*)\.)\s+[^\s`]*\.env\b", re.M)


def _current_guidance() -> list[Path]:
    docs = list((REPO / "docs").glob("*.md"))
    infra = [p for p in INFRA.iterdir() if p.is_file()]
    return sorted(docs + infra + [REPO / "AGENTS.md", REPO / "README.md"]) + sorted(
        (REPO / "skills").glob("*/SKILL.md")
    )


def test_no_unit_loads_the_repo_env() -> None:
    for unit in sorted(INFRA.glob("*.service")):
        for path in re.findall(r"^EnvironmentFile=-?(.*)$", unit.read_text(), re.M):
            assert path == "/etc/address-validator/.env", unit.name


def test_no_current_doc_or_script_sources_an_env_file() -> None:
    hits = [
        f"{p.relative_to(REPO)}: {m.group(0).strip()}"
        for p in _current_guidance()
        if p.exists() and not p.is_symlink()
        for m in SOURCED_ENV.finditer(p.read_text(errors="replace"))
    ]
    assert hits == []


def test_every_load_env_site_lifts_the_vendored_recipe() -> None:
    """The sites name the recipe's path, and the sed range still finds it there."""
    text = (REPO / RECIPE).read_text()
    assert re.search(r"^#   load_env\(\) [{]", text, re.M)
    assert re.search(r"^#   [}]$", text, re.M)
    sites = [p for p in _current_guidance() if p.exists() and "load_env /etc/" in p.read_text()]
    assert len(sites) == 4
    for p in sites:
        assert f"RECIPE={RECIPE}" in p.read_text(), p.relative_to(REPO)
