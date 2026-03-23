"""Unit tests for auth.py dependency behaviour."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from address_validator import auth

_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
_CONFIGURED_KEY = "configured-test-key"


def _mock_request(
    path: str = "/api/test",
    configured_key: str | None = _CONFIGURED_KEY,
) -> MagicMock:
    """Return a minimal mock of a FastAPI/Starlette Request.

    ``configured_key`` is stored on ``req.app.state.api_key`` to simulate the
    value set by the lifespan startup hook.  Pass ``None`` to simulate a
    misconfigured service.
    """
    req = MagicMock()
    req.url.path = path
    req.app.state.api_key = configured_key
    return req


class TestRequireApiKey:
    """Tests for the require_api_key FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_valid_key_accepted(self) -> None:
        result = await auth.require_api_key(_mock_request(), _CONFIGURED_KEY)
        assert result == _CONFIGURED_KEY

    @pytest.mark.asyncio
    async def test_missing_key_raises_401(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.INFO, logger="address_validator.auth"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await auth.require_api_key(_mock_request("/api/parse"), None)
        assert exc_info.value.status_code == 401
        assert "missing API key" in caplog.text
        assert "/api/parse" in caplog.text

    @pytest.mark.asyncio
    async def test_wrong_key_raises_403(self, caplog: pytest.LogCaptureFixture) -> None:
        key = "definitely-wrong-key"
        with (
            caplog.at_level(logging.INFO, logger="address_validator.auth"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await auth.require_api_key(_mock_request("/api/standardize"), key)
        assert exc_info.value.status_code == 403
        assert "invalid API key" in caplog.text
        assert "/api/standardize" in caplog.text

    @pytest.mark.asyncio
    async def test_oversized_key_raises_403(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.INFO, logger="address_validator.auth"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await auth.require_api_key(_mock_request(), "x" * 257)
        assert exc_info.value.status_code == 403
        assert "invalid API key" in caplog.text


class TestApiKeyImportGuard:
    """auth.py is importable without API_KEY; the guard fires at dependency time.

    We verify importability by running a fresh Python subprocess with API_KEY
    deliberately absent from its environment, and verify the runtime guard by
    passing a request with app.state.api_key set to None.
    """

    def test_module_importable_without_api_key(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "API_KEY"}
        env["PYTHONPATH"] = str(Path(_PROJECT_ROOT) / "src")
        result = subprocess.run(
            [sys.executable, "-c", "import address_validator.auth"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_module_importable_with_empty_api_key(self) -> None:
        env = {**os.environ, "API_KEY": "", "PYTHONPATH": str(Path(_PROJECT_ROOT) / "src")}
        result = subprocess.run(
            [sys.executable, "-c", "import address_validator.auth"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.asyncio
    async def test_unconfigured_key_raises_503(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key(_mock_request(configured_key=None), "any-key")
        assert exc_info.value.status_code == 503
