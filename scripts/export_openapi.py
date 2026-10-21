#!/usr/bin/env python3
"""Export the app's OpenAPI schema to the committed ``openapi.json`` (GH #276).

``openapi.json`` at the repo root is the published API contract.
``tests/unit/test_openapi_schema.py`` fails when the app's schema differs from
it, so a change that alters the schema ships the regenerated file in the same
PR.

Usage:
    uv run python scripts/export_openapi.py

Writes the file directly, never to stdout: importing the app configures its
JSON logger on stdout, so a shell redirect would capture log lines too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_PATH = PROJECT_ROOT / "openapi.json"
REGEN_COMMAND = "uv run python scripts/export_openapi.py"

# Ensure the src/ layout is importable when run directly
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def render() -> str:
    """The app's OpenAPI schema, serialized deterministically."""
    from address_validator.main import app  # noqa: PLC0415 — needs the sys.path entry above

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    OPENAPI_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {OPENAPI_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
