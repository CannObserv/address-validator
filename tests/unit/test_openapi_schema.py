"""Drift guard for the committed OpenAPI schema (GH #276).

``openapi.json`` at the repo root is the published API contract, pinned so
every contract change lands as a reviewed diff. This test builds the schema
from the app and fails when it differs from the committed file. It needs no DB
and no network: ``app.openapi()`` reads only the route and model definitions,
and no env var reaches them.

If you change the API contract, regenerate the file and commit it with the
change::

    uv run python scripts/export_openapi.py
"""

import difflib
import logging

import export_openapi
import pytest

from address_validator.main import app

# Enough of the diff to locate the change without flooding the report.
_MAX_DIFF_LINES = 60


def test_committed_schema_matches_app() -> None:
    if not export_openapi.OPENAPI_PATH.is_file():
        pytest.fail(f"openapi.json is missing; generate it with: {export_openapi.REGEN_COMMAND}")
    committed = export_openapi.OPENAPI_PATH.read_text(encoding="utf-8")
    rendered = export_openapi.render()
    if committed == rendered:
        return
    diff = list(
        difflib.unified_diff(
            committed.splitlines(),
            rendered.splitlines(),
            fromfile="openapi.json (committed)",
            tofile="openapi.json (app)",
            lineterm="",
        )
    )
    shown = "\n".join(diff[:_MAX_DIFF_LINES])
    if len(diff) > _MAX_DIFF_LINES:
        shown += f"\n... {len(diff) - _MAX_DIFF_LINES} more diff lines"
    pytest.fail(
        "The app's OpenAPI schema differs from the committed openapi.json. "
        "Regenerate it and commit the file with the schema change:\n"
        f"    {export_openapi.REGEN_COMMAND}\n\n{shown}"
    )


def test_main_writes_rendered_schema(tmp_path, monkeypatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(export_openapi, "OPENAPI_PATH", target)
    monkeypatch.setattr(export_openapi, "PROJECT_ROOT", tmp_path)
    export_openapi.main()
    assert target.read_text(encoding="utf-8") == export_openapi.render()


def test_schema_generation_logs_no_warning(caplog, monkeypatch) -> None:
    """GH #342: schema generation is fault-free, so it must log nothing at WARNING+.

    ``contact.email`` is typed ``EmailStr`` in FastAPI's OpenAPI models; without
    ``email-validator`` installed, every validation of it logs a WARNING.
    """
    monkeypatch.setattr(app, "openapi_schema", None)  # defeat the per-process cache
    with caplog.at_level(logging.WARNING):
        app.openapi()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [], [f"{r.name}: {r.getMessage()}" for r in warnings]
