"""Shared Pydantic models for request and response payloads.

Models with a ``V1`` suffix are the canonical v1 API contract served at
``/api/v1/``.  Field names use geography-neutral terminology
(``region``, ``postal_code``) and responses carry ``api_version``.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ComponentSet(BaseModel):
    """A labelled set of address components tagged with their source specification.

    The ``spec`` and ``spec_version`` fields identify the schema the
    ``values`` keys conform to.  This allows callers to handle component
    dicts from different address standards (USPS Pub 28, Royal Mail PAF,
    etc.) without ambiguity.

    Current spec
    ------------
    ``spec``         = ``"usps-pub28"``
    ``spec_version`` = ``"unknown"`` — the exact edition of USPS
    Publication 28 our ``usps_data/`` tables were sourced from has not
    yet been verified against the USPS website.  This value will be
    updated once verified (see GitHub Epic #2).
    """

    spec: str = Field(
        ...,
        description="Machine identifier for the component schema (e.g. 'usps-pub28').",
        examples=["usps-pub28"],
    )
    spec_version: str = Field(
        ...,
        description="Edition of the spec the values conform to.",
        examples=["unknown", "2024-07"],
    )
    values: dict[str, str] = Field(
        ...,
        description="Labelled address component key/value pairs.",
    )


class ErrorResponse(BaseModel):
    """Structured error payload returned by all /api/v1/* error responses."""

    error: str = Field(
        ...,
        description="Snake_case machine-readable error code.",
        examples=["address_required", "country_not_supported"],
    )
    message: str = Field(
        ...,
        description="Human-readable error description.",
    )
    api_version: Literal["1"] = Field(
        default="1", description="API version that produced this error."
    )


# ---------------------------------------------------------------------------
# Request models (shared across v1 routes)
# ---------------------------------------------------------------------------


def _country_field() -> Field:  # type: ignore[valid-type]
    """Return a fresh ``FieldInfo`` for an ISO 3166-1 alpha-2 country field.

    Called as a default factory at class-definition time so each model
    that uses it gets an independent ``FieldInfo`` instance.  Every v1
    request model that carries a ``country`` field must use both this
    factory *and* inherit from :class:`CountryRequestMixin` to pick up
    the normalisation validator.
    """
    return Field(
        default="US",
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code. Only 'US' is supported in v1.",
        examples=["US"],
    )


class CountryRequestMixin(BaseModel):
    """Mixin that adds a normalised ``country`` field to v1 request models.

    Provides the ``country`` field (ISO 3166-1 alpha-2, default ``"US"``)
    and a ``mode='before'`` validator that uppercases and strips it so
    callers may pass ``"us"`` or ``" US "`` without error.

    All v1 request models that accept a country code should inherit from
    this mixin rather than duplicating the field declaration and validator.
    """

    country: str = _country_field()

    @field_validator("country", mode="before")
    @classmethod
    def normalise_country(cls, v: object) -> str:
        """Uppercase and strip a country code string before Pydantic validation.

        Non-string values are returned unchanged and will fail Pydantic's
        type check in the normal validation pass.
        """
        if isinstance(v, str):
            return v.strip().upper()
        return v  # type: ignore[return-value]


class ParseRequestV1(CountryRequestMixin):
    address: str = Field(..., max_length=1000)


class StandardizeRequestV1(CountryRequestMixin):
    """Accept either a raw address string *or* pre-parsed components.

    When both ``address`` and ``components`` are provided, ``components``
    takes precedence and ``address`` is ignored.
    """

    address: str | None = Field(None, max_length=1000)
    components: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Response models — v1
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    api_version: Literal["1"] = "1"


class ParseResponseV1(BaseModel):
    input: str
    country: str
    components: ComponentSet
    type: str
    warning: str | None = None
    api_version: Literal["1"] = "1"


class StandardizeResponseV1(BaseModel):
    address_line_1: str
    address_line_2: str
    city: str
    region: str
    postal_code: str
    country: str
    standardized: str
    components: ComponentSet
    api_version: Literal["1"] = "1"
