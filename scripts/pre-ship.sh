#!/usr/bin/env bash
# Ship-gate wrapper (#394): wraps the vendored shipping-work gate, never forks
# it. SKILL.md Step 1 resolves this file ahead of the vendored one (#301).
#
# address_validator is not installed into the venv (no [build-system]); the
# unit runs it with PYTHONPATH=src, and pytest gets it from pyproject's
# `pythonpath`. The gate's import check runs a bare `python -c "import
# address_validator"`, so it needs the same path. PYTHONPATH is in the gate's
# stamp key, so a pass recorded here is not reused by an unwrapped run.
#
# No env file is loaded: tests/conftest.py sets everything the suite needs,
# and /etc/address-validator/.env points at the production database.
set -euo pipefail
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
DELEGATE="skills/shipping-work/scripts/pre-ship.sh"
[[ -f "$DELEGATE" ]] || {
  echo "ERROR: vendored gate missing at $DELEGATE" >&2
  echo "       fix: bash .skills/doctor.sh" >&2
  exit 2
}
export PYTHONPATH=src
exec bash "$DELEGATE" "$@"
