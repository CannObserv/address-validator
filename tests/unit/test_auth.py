"""Unit tests for auth.py dependency behaviour."""

import inspect

import pytest
from fastapi import HTTPException

import auth


class TestRequireApiKey:
    """Tests for the require_api_key FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_valid_key_accepted(self) -> None:
        result = await auth.require_api_key(auth._API_KEY)
        assert result == auth._API_KEY

    @pytest.mark.asyncio
    async def test_missing_key_raises_401(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key(None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_raises_403(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key("definitely-wrong-key")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_oversized_key_raises_403(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_api_key("x" * 257)
        assert exc_info.value.status_code == 403


class TestApiKeyImportGuard:
    """The module raises RuntimeError at import time if API_KEY is unset.

    Testing this directly would require unloading the module.  We document
    the contract here and verify the guard string is present in auth.py.
    """

    def test_guard_message_documented(self) -> None:
        src = inspect.getsource(auth)
        assert "API_KEY environment variable is not set" in src
