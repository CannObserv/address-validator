"""Drift guard: the SocratiCode session pin stays a literal, and the docs name it.

The plugin launches `npx -y --prefer-online ${SOCRATICODE_SPEC:-socraticode@latest}`.
`SOCRATICODE_SPEC` in `.claude/settings.json` pins that launch to the driver's
pre-installed version (GH #223), so no session start installs a server — the
memory peak GH #214 measured on this no-swap host.

Two ways it silently regresses in the repo, both guarded here:

- the spec floats again (`@latest`, a range, a bare name, or the key dropped by
  a hand edit or an `init-socraticode` re-run) and every session start is an
  install again, with nothing failing;
- the version moves and `docs/HOST-MEMORY.md` goes on naming the old one, so the
  re-pin and verification instructions describe a build nobody runs.

The settings block only *declares* the spec. The VS Code extension expands the
plugin's args before merging that block, so what pins the launch is the
machine-scoped `claudeCode.environmentVariables` setting
(gregoryfoster/skills#332). That is host state, so its test runs only on a VS
Code remote host and skips elsewhere (CI). It guards a third way to regress: a
re-pin that moves the repo's value but not the machine's, which every check
reading its own environment then reports as pinned.

The pre-install under `~/.socraticode/pin` is host state too; comparing it is
`preflight.sh --check`'s job.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
DOC = REPO_ROOT / "docs" / "HOST-MEMORY.md"
SECTION_HEADING = "## Don't install a SocratiCode server at launch"
VSCODE_SERVER = Path.home() / ".vscode-server"
MACHINE_SETTINGS = VSCODE_SERVER / "data" / "Machine" / "settings.json"

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
        f"{DOC.name} → '{SECTION_HEADING[3:]}' does not say the launches are pinned to {version}"
    )
    stale = sorted(set(LITERAL_SPEC_RE.findall(section)) - {version})
    assert not stale, (
        f"{DOC.name} → '{SECTION_HEADING[3:]}' still names socraticode@{', @'.join(stale)} "
        f"while SOCRATICODE_SPEC pins {version}"
    )


TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _read_jsonc(path: Path) -> dict:
    # VS Code keeps its settings as JSONC: drop whole-line comments and trailing
    # commas, the two forms its editor accepts that json.loads does not. Inline
    # comments are left alone; a value may carry `//` (a URL).
    lines = path.read_text(encoding="utf-8").splitlines()
    text = "\n".join(ln for ln in lines if not ln.lstrip().startswith("//"))
    try:
        return json.loads(TRAILING_COMMA_RE.sub(r"\1", text))
    except json.JSONDecodeError as e:
        raise AssertionError(f"{path} does not parse as JSONC: {e}") from e


@pytest.mark.skipif(not VSCODE_SERVER.is_dir(), reason="not a VS Code remote host")
def test_vscode_launches_claude_with_the_session_spec() -> None:
    spec = f"socraticode@{_pinned_version()}"
    assert MACHINE_SETTINGS.is_file(), (
        f"{MACHINE_SETTINGS} is missing — the session launches socraticode@latest; "
        f"set claudeCode.environmentVariables SOCRATICODE_SPEC={spec} (docs/HOST-MEMORY.md)"
    )
    entries = _read_jsonc(MACHINE_SETTINGS).get("claudeCode.environmentVariables", [])
    values = [e.get("value") for e in entries if e.get("name") == "SOCRATICODE_SPEC"]
    assert values == [spec], (
        f"{MACHINE_SETTINGS} sets SOCRATICODE_SPEC to {values or 'nothing'}, "
        f"but .claude/settings.json declares {spec} — only the machine setting reaches "
        "the plugin's launch (gregoryfoster/skills#332)"
    )
