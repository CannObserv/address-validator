"""Shared Pydantic models for request and response payloads."""

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

class ParseRequest(BaseModel):
    address: str = Field(..., max_length=1000)


class ParseResponse(BaseModel):
    input: str
    components: dict[str, str]
    type: str
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Standardize
# ---------------------------------------------------------------------------

class StandardizeRequest(BaseModel):
    """Accept either a raw address string *or* pre-parsed components.

    When both ``address`` and ``components`` are provided, ``components``
    takes precedence and ``address`` is ignored.
    """
    address: Optional[str] = Field(None, max_length=1000)
    components: Optional[dict[str, str]] = None


class StandardizeResponse(BaseModel):
    address_line_1: str
    address_line_2: str
    city: str
    state: str
    zip_code: str
    standardized: str
    components: dict[str, str]
