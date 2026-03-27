"""Test a trained model against regression test cases.

Optionally rebuilds a previous model from its manifest for comparison,
proving the new model fixes issues the old model had.

Usage:
    python scripts/model/test_model.py --model training/models/usaddr-multi-unit.crfsuite
    python scripts/model/test_model.py \\
        --model training/models/usaddr-multi-unit.crfsuite \\
        --compare-manifest training/manifests/2026-03-27-baseline.json
    python scripts/model/test_model.py \\
        --model training/models/usaddr-multi-unit.crfsuite --run-pytest
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pycrfsuite
import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_CASES_DIR = PROJECT_ROOT / "training" / "test_cases"


def _load_tagger(model_path: str) -> pycrfsuite.Tagger:
    """Load a CRF model into a new Tagger instance."""
    tagger = pycrfsuite.Tagger()
    tagger.open(model_path)
    return tagger


def _parse_with_tagger(tagger: pycrfsuite.Tagger, address: str) -> list[tuple[str, str]]:
    """Parse an address using a specific tagger (not the global singleton)."""
    original_tagger = usaddress.TAGGER
    try:
        usaddress.TAGGER = tagger
        return usaddress.parse(address)
    except usaddress.RepeatedLabelError as exc:
        return list(exc.parsed_string)
    finally:
        usaddress.TAGGER = original_tagger


def _load_test_cases(path: Path) -> list[dict]:
    """Load test cases from CSV.

    Expected columns: raw_address, expected_labels (JSON array of [token, label] pairs)
    Optional: description, should_fail_old_model (bool)
    """
    cases = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            case = {
                "raw_address": row["raw_address"],
                "expected_labels": json.loads(row["expected_labels"]),
                "description": row.get("description", ""),
                "should_fail_old": row.get("should_fail_old_model", "false").lower() == "true",
            }
            cases.append(case)
    return cases


def _compare_labels(
    actual: list[tuple[str, str]],
    expected: list[tuple[str, str]],
) -> tuple[bool, list[str]]:
    """Compare actual vs expected labels. Returns (passed, list of diffs)."""
    diffs = []
    passed = True

    max_len = max(len(actual), len(expected))
    for i in range(max_len):
        a_tok, a_lab = actual[i] if i < len(actual) else ("MISSING", "MISSING")
        _e_tok, e_lab = expected[i] if i < len(expected) else ("EXTRA", "EXTRA")

        if a_lab != e_lab:
            diffs.append(f"  token='{a_tok}': got '{a_lab}', expected '{e_lab}'")
            passed = False

    return passed, diffs


def _run_tests(
    tagger: pycrfsuite.Tagger,
    test_cases: list[dict],
) -> tuple[int, int, list[str]]:
    """Run test cases against a tagger. Returns (passed, failed, details)."""
    passed = 0
    failed = 0
    details = []

    for case in test_cases:
        actual = _parse_with_tagger(tagger, case["raw_address"])
        ok, diffs = _compare_labels(actual, case["expected_labels"])

        if ok:
            passed += 1
            details.append(f"  PASS: {case['raw_address']}")
        else:
            failed += 1
            details.append(f"  FAIL [{case['description']}]: {case['raw_address']}")
            details.extend(diffs)

    return passed, failed, details


def main() -> None:  # noqa: PLR0912 PLR0915
    parser = argparse.ArgumentParser(description="Test a trained usaddress model")
    parser.add_argument("--model", required=True, help="Path to the .crfsuite model to test")
    parser.add_argument("--compare-manifest", help="Manifest of old model to compare against")
    parser.add_argument("--test-dir", default=str(TEST_CASES_DIR), help="Directory with test CSVs")
    parser.add_argument(
        "--run-pytest", action="store_true", help="Also run the project pytest suite"
    )
    args = parser.parse_args()

    # Load test cases
    test_dir = Path(args.test_dir)
    test_files = sorted(test_dir.glob("*.csv"))
    if not test_files:
        print(f"No test case CSVs found in {test_dir}")
        print(
            "Tip: create test cases in training/test_cases/ using "
            "scripts/model/label.py --test-output"
        )
        if not args.run_pytest:
            sys.exit(0)

    all_cases: list[dict] = []
    for tf in test_files:
        cases = _load_test_cases(tf)
        print(f"Loaded {len(cases)} test cases from {tf.name}")
        all_cases.extend(cases)

    # Test new model
    model_path = args.model
    if not Path(model_path).exists():
        print(f"Error: model file not found: {model_path}", file=sys.stderr)
        sys.exit(1)

    new_tagger = _load_tagger(model_path)

    if all_cases:
        print(f"\n{'=' * 60}")
        print(f"Testing NEW model: {model_path}")
        print(f"{'=' * 60}")
        new_passed, new_failed, new_details = _run_tests(new_tagger, all_cases)
        for d in new_details:
            print(d)
        print(f"\nNEW model: {new_passed} passed, {new_failed} failed")

    # Optionally compare against old model from manifest
    if args.compare_manifest:
        manifest_path = Path(args.compare_manifest)
        if not manifest_path.exists():
            print(f"Error: manifest not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)

        with manifest_path.open() as f:
            old_manifest = json.load(f)

        old_model_name = old_manifest.get("output_model", "")
        old_model_path = PROJECT_ROOT / "training" / "models" / old_model_name

        if not old_model_path.exists():
            print(f"\nOld model {old_model_path} not found.")
            print(
                "Rebuild it with: python scripts/model/train.py --name <name> --description <desc>"
            )
            print("Skipping comparison.")
        elif all_cases:
            print(f"\n{'=' * 60}")
            print(f"Testing OLD model: {old_model_path}")
            print(f"{'=' * 60}")
            old_tagger = _load_tagger(str(old_model_path))
            old_passed, old_failed, old_details = _run_tests(old_tagger, all_cases)
            for d in old_details:
                print(d)
            print(f"\nOLD model: {old_passed} passed, {old_failed} failed")

            # Show improvement for targeted cases
            improvement_cases = [c for c in all_cases if c["should_fail_old"]]
            if improvement_cases:
                print(f"\n{'=' * 60}")
                print("Improvement targets (should_fail_old_model=true):")
                print(f"{'=' * 60}")
                for case in improvement_cases:
                    old_result = _parse_with_tagger(old_tagger, case["raw_address"])
                    new_result = _parse_with_tagger(new_tagger, case["raw_address"])
                    old_ok, _ = _compare_labels(old_result, case["expected_labels"])
                    new_ok, _ = _compare_labels(new_result, case["expected_labels"])
                    if not old_ok and new_ok:
                        status = "FIXED"
                    elif old_ok and not new_ok:
                        status = "REGRESSION"
                    else:
                        status = "UNCHANGED"
                    print(f"  {status}: {case['raw_address']}")

    # Optionally run pytest with the new model active
    if args.run_pytest:
        print(f"\n{'=' * 60}")
        print("Running project test suite with new model...")
        print(f"{'=' * 60}")
        env = {**os.environ, "CUSTOM_MODEL_PATH": model_path}
        result = subprocess.run(
            ["uv", "run", "pytest", "--no-cov", "-x"],  # noqa: S607
            env=env,
            check=False,
        )
        if result.returncode != 0:
            print("\nProject tests FAILED with new model!", file=sys.stderr)
            sys.exit(1)
        print("Project tests PASSED with new model.")

    # Exit with failure if new model has failures on regression cases
    if all_cases and new_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
