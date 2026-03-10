"""Shared Pydantic request and response models for the Address Validator API.

All active models are served at ``/api/v1/`` and use geography-neutral
field names (``region``, ``postal_code``).  Response models carry an
``api_version`` field set to ``"1"``.

Note: ``api_version`` in response bodies refers to the route namespace
(``/api/v1/``), not the overall service version declared in ``main.py``.
The two signals are intentionally decoupled.
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
# Request models — v1 validate
# ---------------------------------------------------------------------------


class ValidateRequestV1(CountryRequestMixin):
    """Request body for POST /api/v1/validate.

    Accepts individual address components rather than a raw string so
    callers who have already parsed/standardized can skip that step.
    ``address`` is the street line (number + name + suffix + unit).
    ``region`` follows the geography-neutral convention used throughout
    v1 (equivalent to state for US addresses).
    """

    address: str = Field(..., max_length=1000)
    city: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)


# ---------------------------------------------------------------------------
# Response models — v1
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    api_version: Literal["1"] = "1"


class ValidationResult(BaseModel):
    """Provider-returned validation outcome metadata.

    ``status`` is the primary machine-readable result:

    * ``confirmed``                   — DPV code Y: fully confirmed delivery point.
    * ``confirmed_missing_secondary`` — DPV code S: building confirmed, unit missing.
    * ``confirmed_bad_secondary``     — DPV code D: building confirmed, unit unrecognised.
    * ``not_confirmed``               — DPV code N: address not found in USPS database.
    * ``unavailable``                 — provider not configured or unreachable.
    """

    status: Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
        "unavailable",
    ]
    dpv_match_code: Literal["Y", "S", "D", "N"] | None = Field(
        default=None,
        description="USPS DPV match code. Y=confirmed, S=missing secondary, "
        "D=bad secondary, N=not found. None when unavailable.",
    )
    provider: str | None = Field(
        default=None,
        description="Provider that performed validation ('usps', 'google', etc.). "
        "None when unavailable.",
    )


class ValidateResponseV1(BaseModel):
    """Response body for POST /api/v1/validate.

    Mirrors the structure of ``StandardizeResponseV1``.  Address fields are
    ``str | None`` because corrected components are only present when the
    provider returns a confirmed or corrected address.

    ``postal_code`` is the jurisdiction-neutral postal identifier.  For US
    addresses it carries the full ZIP+4 (e.g. ``"62701-1234"``) when the
    provider returns it, or the 5-digit ZIP otherwise.

    ``vacant`` and other USPS-specific indicators appear in
    ``components.values`` when the provider returns them.
    """

    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str
    validated: str | None = Field(
        default=None,
        description="Single-line canonical address using two-space separator convention.",
    )
    components: ComponentSet | None = None
    validation: ValidationResult
    latitude: float | None = None
    longitude: float | None = None
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["1"] = "1"


class ParseResponseV1(BaseModel):
    input: str
    country: str
    components: ComponentSet
    type: str
    warnings: list[str] = Field(default_factory=list)
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
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["1"] = "1"
