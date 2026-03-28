"""Train a custom usaddress CRF model from labeled XML data.

Combines upstream training data with custom labeled XML and runs
parserator train. Writes a manifest for deterministic reconstruction.

Usage:
    python scripts/model/train.py --name multi-unit --description "Multi-unit designator handling"
    python scripts/model/train.py --name multi-unit --custom-only
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRAINING_DIR = PROJECT_ROOT / "training"
SESSIONS_DIR = TRAINING_DIR / "sessions"
MODELS_DIR = TRAINING_DIR / "models"


def _find_upstream_training_files() -> list[Path]:
    """Find XML training files from the installed usaddress package source."""
    pkg_dir = Path(usaddress.__file__).parent
    # Check common locations for training data
    candidates = [
        TRAINING_DIR / "upstream",  # local copy of upstream training data
        pkg_dir.parent / "training",  # pip install -e / source layout
        pkg_dir / "training",
    ]
    for candidate in candidates:
        if candidate.exists():
            xmls = sorted(candidate.glob("*.xml"))
            if xmls:
                return xmls

    print(
        "Warning: could not find upstream training data. "
        "Download it with: curl -sL https://raw.githubusercontent.com/datamade/usaddress/"
        "master/training/labeled.xml -o training/upstream/labeled.xml --create-dirs",
        file=sys.stderr,
    )
    return []


def _find_custom_training_files(session_dir: Path) -> list[Path]:
    """Find training XML files in a session directory."""
    xmls = sorted(session_dir.glob("training-data*.xml"))
    if not xmls:
        # Fallback: any XML in the session dir
        xmls = sorted(session_dir.glob("*.xml"))
    return xmls


def _build_manifest(
    name: str,
    description: str,
    session_dir: str,
    training_files: list[tuple[str, str]],
    test_files: list[str],
    output_model: str,
    rationale_file: str | None = None,
) -> dict:
    """Build a training manifest dict."""
    version = getattr(usaddress, "__version__", "unknown")
    return {
        "id": f"{datetime.now(UTC).strftime('%Y-%m-%d')}-{name}",
        "description": description,
        "usaddress_version": version,
        "session_dir": session_dir,
        "training_files": [f"{src}:{path}" for src, path in training_files],
        "test_files": test_files,
        "created_at": datetime.now(UTC).isoformat(),
        "output_model": output_model,
        "deployed": False,
        "upstream_pr": None,
        "rationale_file": rationale_file,
        "performance_file": None,
    }


def main() -> None:  # noqa: PLR0912 PLR0915
    parser = argparse.ArgumentParser(description="Train custom usaddress model")
    parser.add_argument("--name", required=True, help="Short name for this training run")
    parser.add_argument("--description", required=True, help="What this training addresses")
    parser.add_argument("--custom-only", action="store_true", help="Train on custom data only")
    parser.add_argument(
        "--session-dir",
        help="Session dir with training data (default: auto-created)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help="Specific custom XML files (overrides session dir search)",
    )
    args = parser.parse_args()

    # Determine session directory
    if args.session_dir:
        session_dir = Path(args.session_dir)
    else:
        ts = datetime.now(UTC).strftime("%Y_%m_%d-%H_%M")
        session_dir = SESSIONS_DIR / f"{ts}-{args.name.replace(' ', '_')}"
    session_dir.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Collect training files
    training_file_entries: list[tuple[str, str]] = []
    training_paths: list[Path] = []

    if not args.custom_only:
        upstream_files = _find_upstream_training_files()
        for f in upstream_files:
            training_file_entries.append(("upstream", str(f.name)))
            training_paths.append(f)

    if args.files:
        custom_files = [Path(f) for f in args.files]
    else:
        custom_files = _find_custom_training_files(session_dir)
    if not custom_files:
        print(f"Error: no training XML found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    for f in custom_files:
        training_file_entries.append(("custom", str(f.name)))
        training_paths.append(f)

    print(f"Session: {session_dir}")
    print(f"Training with {len(training_paths)} files:")
    for src, name in training_file_entries:
        print(f"  [{src}] {name}")

    # Find test files in session dir
    test_files = sorted(str(f.name) for f in session_dir.glob("test-cases*.csv"))

    # Backup current model
    current_model = Path(usaddress.MODEL_PATH)
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_path = MODELS_DIR / f"usaddr-backup-{timestamp}.crfsuite"
    if current_model.exists():
        shutil.copy2(current_model, backup_path)
        print(f"Backed up current model to {backup_path}")

    # Run parserator train
    training_arg = ",".join(str(p) for p in training_paths)
    cmd = ["uv", "run", "parserator", "train", training_arg, "usaddress"]
    print(f"\nRunning: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"\nTraining failed (exit code {result.returncode})", file=sys.stderr)
        if backup_path.exists():
            shutil.copy2(backup_path, current_model)
            print("Restored backup model.")
        sys.exit(1)

    # Copy trained model to our models directory
    output_name = f"usaddr-{args.name}.crfsuite"
    output_path = MODELS_DIR / output_name
    if current_model.exists():
        shutil.copy2(current_model, output_path)
        print(f"Saved trained model to {output_path}")

    # Restore the original model
    if backup_path.exists():
        shutil.copy2(backup_path, current_model)
        print("Restored original bundled model.")

    # Check for rationale file in session dir
    rationale_file = None
    rationale_path = session_dir / "rationale.md"
    if rationale_path.exists():
        rationale_file = "rationale.md"
        print(f"Found rationale at {rationale_path}")

    # Write manifest to session dir
    manifest = _build_manifest(
        name=args.name,
        description=args.description,
        session_dir=str(session_dir),
        training_files=training_file_entries,
        test_files=test_files,
        output_model=output_name,
        rationale_file=rationale_file,
    )
    manifest_path = session_dir / "manifest.json"
    with manifest_path.open("w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
