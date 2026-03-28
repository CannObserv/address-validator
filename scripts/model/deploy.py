"""Deploy a trained usaddress model to the application.

Copies the model to the committed deployment path and validates it loads.

Usage:
    python scripts/model/deploy.py --model training/models/usaddr-multi-unit.crfsuite
    python scripts/model/deploy.py --model training/models/usaddr-multi-unit.crfsuite --restart
    python scripts/model/deploy.py --model training/models/usaddr-multi-unit.crfsuite --restart
        --smoke-test
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pycrfsuite
import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_DIR = PROJECT_ROOT / "src" / "address_validator" / "custom_model"
DEPLOY_PATH = DEPLOY_DIR / "usaddr-custom.crfsuite"
MANIFESTS_DIR = PROJECT_ROOT / "training" / "manifests"


def _validate_model(model_path: Path) -> bool:
    """Verify the model file loads and can parse a simple address."""
    original_tagger = usaddress.TAGGER
    try:
        tagger = pycrfsuite.Tagger()
        tagger.open(str(model_path))

        usaddress.TAGGER = tagger
        result = usaddress.parse("123 Main St, Springfield, IL 62701")
        if not result:
            print("Error: model produced no output for smoke test address", file=sys.stderr)
            return False

        print(f"Model validation passed ({len(tagger.labels())} labels)")
        return True
    except Exception as exc:
        print(f"Error: model validation failed: {exc}", file=sys.stderr)
        return False
    finally:
        usaddress.TAGGER = original_tagger


def _update_manifest_deployed(model_name: str) -> None:
    """Mark the corresponding manifest as deployed."""
    for manifest_file in sorted(MANIFESTS_DIR.glob("*.json")):
        with manifest_file.open() as f:
            manifest = json.load(f)
        if manifest.get("output_model") == model_name:
            manifest["deployed"] = True
            with manifest_file.open("w") as f:
                json.dump(manifest, f, indent=2)
            print(f"Updated manifest {manifest_file.name}: deployed=true")
            return
    print(f"Warning: no manifest found for model '{model_name}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy trained usaddress model")
    parser.add_argument("--model", required=True, help="Path to .crfsuite model to deploy")
    parser.add_argument("--restart", action="store_true", help="Restart the service after deploy")
    parser.add_argument("--smoke-test", action="store_true", help="Run health check after restart")
    parser.add_argument(
        "--health-url",
        default="http://localhost:8000/api/v1/health",
        help="Health check URL for --smoke-test (default: http://localhost:8000/api/v1/health)",
    )
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"Error: model file not found: {model_path}", file=sys.stderr)
        sys.exit(1)

    # Validate the model loads correctly
    if not _validate_model(model_path):
        sys.exit(1)

    # Copy to deployment path
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_path, DEPLOY_PATH)
    print(f"Deployed model to {DEPLOY_PATH}")

    # Update manifest
    _update_manifest_deployed(model_path.name)

    # Show next steps
    print("\n--- Next steps ---")
    print(f"1. Ensure CUSTOM_MODEL_PATH={DEPLOY_PATH} in /etc/address-validator/env")

    if args.restart:
        print("\nRestarting address-validator service...")
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "address-validator"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"Error restarting service: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        print("Service restarted.")

        if args.smoke_test:
            time.sleep(2)
            print("\nRunning smoke test...")
            try:
                with urllib.request.urlopen(args.health_url, timeout=5) as resp:  # noqa: S310
                    body = json.loads(resp.read())
                print(f"Health check: {body}")
                if body.get("status") != "ok":
                    print("Warning: service health is not 'ok'", file=sys.stderr)
            except Exception as exc:
                print(f"Smoke test failed: {exc}", file=sys.stderr)
                sys.exit(1)
    else:
        print("2. Run: sudo systemctl restart address-validator")
        print("3. Verify: journalctl -u address-validator -n 20")
        print(f"   Look for: 'loaded custom usaddress model: {DEPLOY_PATH}'")


if __name__ == "__main__":
    main()
