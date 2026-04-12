"""Shared FastAPI dependency functions for route handlers."""

from fastapi import Request

from address_validator.services.libpostal_client import LibpostalClient
from address_validator.services.validation.registry import ProviderRegistry


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def get_libpostal_client(request: Request) -> LibpostalClient | None:
    return getattr(request.app.state, "libpostal_client", None)
