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
    """The module raises RuntimeError at import time if API_KEY is unset.

    We verify the guard fires by running a fresh Python subprocess with
    API_KEY deliberately absent from its environment.
    """

    def test_missing_key_raises_on_import(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "API_KEY"}
        env["PYTHONPATH"] = _PROJECT_ROOT
        result = subprocess.run(
            [sys.executable, "-c", "import auth"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "API_KEY" in result.stderr

    def test_empty_key_raises_on_import(self) -> None:
        env = {**os.environ, "API_KEY": "", "PYTHONPATH": _PROJECT_ROOT}
        result = subprocess.run(
            [sys.executable, "-c", "import auth"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "API_KEY" in result.stderr
