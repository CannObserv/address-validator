"""Drift guard: the SocratiCode session pin stays a literal, and the docs name it.

The plugin launches `npx -y --prefer-online ${SOCRATICODE_SPEC:-socraticode@latest}`.
`SOCRATICODE_SPEC` in `.claude/settings.json` pins that launch to the driver's
pre-installed version (GH #223), so no session start installs a server — the
memory peak GH #214 measured on this no-swap host.

Two ways it silently regresses, both guarded here:

- the spec floats again (`@latest`, a range, a bare name, or the key dropped by
  a hand edit or an `init-socraticode` re-run) and every session start is an
  install again, with nothing failing;
- the version moves and `docs/DEPLOYMENT.md` goes on naming the old one, so the
  re-pin and verification instructions describe a build nobody runs.

The pre-install under `~/.socraticode/pin` is host state, not repo state; that
half of "re-pin both together" is `preflight.sh --check`'s to compare.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
DOC = REPO_ROOT / "docs" / "DEPLOYMENT.md"
SECTION_HEADING = "### Don't install a SocratiCode server at launch"

LITERAL_SPEC_RE = re.compile(r"socraticode@(\d+\.\d+\.\d+)")


def _pinned_version() -> str:
    env = json.loads(SETTINGS.read_text(encoding="utf-8")).get("env", {})
    spec = env.get("SOCRATICODE_SPEC")
    assert spec is not None, (
        f"{SETTINGS.name} has no env.SOCRATICODE_SPEC — the plugin session is "
        "back to launching socraticode@latest, an install at every session start (GH #223)"
    )
    m = LITERAL_SPEC_RE.fullmatch(spec)
    assert m, (
        f"SOCRATICODE_SPEC={spec!r} is not a literal socraticode@x.y.z — "
        "a tag or range resolves at launch, so it installs whenever the package moves"
    )
    return m.group(1)


def _section() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(SECTION_HEADING)
    end = text.find("\n## ", start)
    return text[start : end if end != -1 else len(text)]


def test_session_spec_is_a_literal_version() -> None:
    _pinned_version()


def test_deployment_doc_names_the_pinned_version() -> None:
    version = _pinned_version()
    section = _section()
    assert f"pinned to **{version}**" in section, (
        f"{DOC.name} → '{SECTION_HEADING[4:]}' does not say the launches are pinned to {version}"
    )
    stale = sorted(set(LITERAL_SPEC_RE.findall(section)) - {version})
    assert not stale, (
        f"{DOC.name} → '{SECTION_HEADING[4:]}' still names socraticode@{', @'.join(stale)} "
        f"while SOCRATICODE_SPEC pins {version}"
    )
