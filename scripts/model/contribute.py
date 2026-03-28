"""Contribute training data to the usaddress upstream repo.

Two-stage process:
  1. Push training + test XML to our fork (fast, unblocked)
  2. Open a PR from our fork to datamade/usaddress (gated, explicit confirmation)

Usage:
    python scripts/model/contribute.py --name multi-unit --stage fork
    python scripts/model/contribute.py --name multi-unit --stage upstream
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SESSIONS_DIR = PROJECT_ROOT / "training" / "sessions"

# Our fork — update when fork is created
FORK_REPO = ""  # e.g. "CannObserv/usaddress"
UPSTREAM_REPO = "datamade/usaddress"


def _find_manifest(name: str) -> tuple[dict, Path] | None:
    """Find a manifest by name (matches anywhere in the manifest ID)."""
    for manifest_file in sorted(SESSIONS_DIR.rglob("manifest.json"), reverse=True):
        with manifest_file.open() as f:
            manifest = json.load(f)
        if name in manifest.get("id", ""):
            return manifest, manifest_file.parent
    return None


def _first_address_from_xml(xml_path: Path) -> str | None:
    """Extract the first address string from a training XML file."""
    try:
        tree = ET.parse(xml_path)  # noqa: S314
        root = tree.getroot()
        first_addr = root.find("AddressString")
        if first_addr is not None:
            return " ".join(child.text or "" for child in first_addr)
    except Exception:  # noqa: S110
        pass
    return None


def _generate_pr_body(manifest: dict, training_xml: Path, test_xml: Path | None) -> str:
    """Generate a PR body following upstream conventions (based on merged PRs)."""
    description = manifest["description"]
    usaddress_version = manifest.get("usaddress_version", "unknown")

    # Build before/after example from first training address
    example_lines: list[str] = []
    address = _first_address_from_xml(training_xml)
    if address:
        try:
            result = usaddress.parse(address)
            example_lines.append("```python")
            example_lines.append(">>> import usaddress")
            example_lines.append(f'>>> usaddress.parse("{address}")')
            example_lines.append(str(result))
            example_lines.append("```")
        except usaddress.RepeatedLabelError as exc:
            example_lines.append("```python")
            example_lines.append(">>> import usaddress")
            example_lines.append(f'>>> usaddress.parse("{address}")')
            example_lines.append("# Raises RepeatedLabelError with current model")
            example_lines.append(f"# parsed_string: {list(exc.parsed_string)[:4]}...")
            example_lines.append("```")

    examples = "\n".join(example_lines) if example_lines else "_See training data for examples._"

    test_data_line = f"`measure_performance/test_data/{test_xml.name}`" if test_xml else "_N/A_"

    return f"""## Overview

{description}

## Problem

Using `usaddress ({usaddress_version})`, the following address patterns are parsed incorrectly:

{examples}

## Training data

- Training: `training/{training_xml.name}`
- Test: {test_data_line}

## Testing

```bash
pip install -e ".[dev]"
parserator train training/{training_xml.name} usaddress
pytest
```
"""


def _stage_fork(name: str, manifest: dict, session_dir: Path) -> None:
    """Show instructions for pushing training data to our fork."""
    if not FORK_REPO:
        print(
            "FORK_REPO is not configured in scripts/model/contribute.py.\n"
            "Steps to set up:\n"
            "  1. Fork datamade/usaddress on GitHub\n"
            "  2. Set FORK_REPO = '<your-org>/usaddress' in this script\n"
            "  3. Clone the fork and add the training XML files\n"
            "  4. Re-run this command",
            file=sys.stderr,
        )
        sys.exit(1)

    custom_files = [
        f.split(":", 1)[1] for f in manifest.get("training_files", []) if f.startswith("custom:")
    ]
    if not custom_files:
        print("Error: no custom training files in manifest", file=sys.stderr)
        sys.exit(1)

    print(f"\nFiles to push to fork {FORK_REPO}:")
    for fname in custom_files:
        training_path = session_dir / fname
        if training_path.exists():
            print(f"  training/{fname}")
    test_path = session_dir / "test-data.xml"
    if test_path.exists():
        print("  measure_performance/test_data/test-data.xml")

    print(
        "\nThis requires a local clone of the fork. "
        "Copy the files above into the fork's training/ and "
        "measure_performance/test_data/ directories, then push."
    )


def _stage_upstream(name: str, manifest: dict, session_dir: Path, branch: str = "main") -> None:
    """Display the upstream PR body and either open it via gh or print manual instructions."""
    if not FORK_REPO:
        print("Error: FORK_REPO not configured. Run --stage fork first.", file=sys.stderr)
        sys.exit(1)

    custom_files = [
        f.split(":", 1)[1] for f in manifest.get("training_files", []) if f.startswith("custom:")
    ]
    if not custom_files:
        print("Error: no custom training files in manifest", file=sys.stderr)
        sys.exit(1)

    training_xml = session_dir / custom_files[0]
    test_xml_path = session_dir / "test-data.xml"
    test_xml = test_xml_path if test_xml_path.exists() else None

    pr_title = manifest["description"]
    if len(pr_title) > 70:  # noqa: PLR2004
        pr_title = pr_title[:67] + "..."

    pr_body = _generate_pr_body(manifest, training_xml, test_xml)

    fork_org = FORK_REPO.split("/", maxsplit=1)[0]

    print("=" * 60)
    print(f"PR Title: {pr_title}")
    print("=" * 60)
    print(pr_body)
    print(f"Target: {FORK_REPO} → {UPSTREAM_REPO}")
    print("=" * 60)
    print(
        f"\nFork branch: {fork_org}:{branch}\n"
        "Only proceed when training data is correct, complete, and pushed to your fork.\n"
        "  [o] Open PR now via gh cli  (requires fork branch already pushed)\n"
        "  [i] Show manual instructions\n"
        "  [n] Abort\n"
    )
    print("Choice [o/i/n]: ", end="")
    choice = input().strip().lower()

    if choice in {"n", ""}:
        print("Aborted.")
        sys.exit(0)
    elif choice == "i":
        print("\nManual steps to open the PR:")
        print(f"  1. Ensure your fork branch is pushed: git push {fork_org} {branch}")
        print(f"  2. gh pr create --repo {UPSTREAM_REPO} \\")
        print(f"       --head {fork_org}:{branch} \\")
        print(f'       --title "{pr_title}" \\')
        print("       --body $'<paste body above>'")
    elif choice == "o":
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "gh",
                "pr",
                "create",
                "--repo",
                UPSTREAM_REPO,
                "--head",
                f"{fork_org}:{branch}",
                "--title",
                pr_title,
                "--body",
                pr_body,
            ],
            check=False,
        )
        if result.returncode != 0:
            print("Error: gh pr create failed. Check output above.", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown choice '{choice}'. Aborted.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Contribute training data upstream")
    parser.add_argument("--name", required=True, help="Training run name (matches manifest ID)")
    parser.add_argument(
        "--stage",
        required=True,
        choices=["fork", "upstream"],
        help="fork: push to our fork | upstream: open PR to datamade/usaddress",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Branch in the fork to use as PR head (default: main)",
    )
    args = parser.parse_args()

    result = _find_manifest(args.name)
    if not result:
        print(f"Error: no manifest found matching '{args.name}'", file=sys.stderr)
        sys.exit(1)

    manifest, session_dir = result
    print(f"Using manifest: {manifest['id']}")
    print(f"Session: {session_dir}")
    print(f"Description: {manifest['description']}")

    if args.stage == "fork":
        _stage_fork(args.name, manifest, session_dir)
    else:
        _stage_upstream(args.name, manifest, session_dir, branch=args.branch)


if __name__ == "__main__":
    main()
