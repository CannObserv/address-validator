"""Unit tests for auth.py dependency behaviour."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import auth

_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)


def _mock_request(path: str = "/api/test") -> MagicMock:
    """Return a minimal mock of a FastAPI/Starlette Request."""
    req = MagicMock()
    req.url.path = path
    return req


class TestRequireApiKey:
    """Tests for the require_api_key FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_valid_key_accepted(self) -> None:
        assert auth._API_KEY is not None, "conftest must set API_KEY before this test runs"
        result = await auth.require_api_key(_mock_request(), auth._API_KEY)
        assert result == auth._API_KEY

    @pytest.mark.asyncio
    async def test_missing_key_raises_401(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="auth"), pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key(_mock_request("/api/parse"), None)
        assert exc_info.value.status_code == 401
        assert "missing API key" in caplog.text
        assert "/api/parse" in caplog.text

    @pytest.mark.asyncio
    async def test_wrong_key_raises_403(self, caplog: pytest.LogCaptureFixture) -> None:
        key = "definitely-wrong-key"
        with caplog.at_level(logging.INFO, logger="auth"), pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key(_mock_request("/api/standardize"), key)
        assert exc_info.value.status_code == 403
        assert "invalid API key" in caplog.text
        assert "/api/standardize" in caplog.text

    @pytest.mark.asyncio
    async def test_oversized_key_raises_403(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="auth"), pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key(_mock_request(), "x" * 257)
        assert exc_info.value.status_code == 403
        assert "invalid API key" in caplog.text


class TestApiKeyImportGuard:
    """auth.py is importable without API_KEY; the guard fires at dependency time.

    We verify importability by running a fresh Python subprocess with API_KEY
    deliberately absent from its environment, and verify the runtime guard by
    calling require_api_key with _API_KEY patched to None.
    """

    def test_module_importable_without_api_key(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "API_KEY"}
        env["PYTHONPATH"] = _PROJECT_ROOT
        result = subprocess.run(
            [sys.executable, "-c", "import auth"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_module_importable_with_empty_api_key(self) -> None:
        env = {**os.environ, "API_KEY": "", "PYTHONPATH": _PROJECT_ROOT}
        result = subprocess.run(
            [sys.executable, "-c", "import auth"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.asyncio
    async def test_unconfigured_key_raises_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth, "_API_KEY", None)
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key(_mock_request(), "any-key")
        assert exc_info.value.status_code == 503
