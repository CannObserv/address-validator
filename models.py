"""Shared Pydantic models for request and response payloads.

Versioning
----------
Models without a version suffix (e.g. ``ParseResponse``) are the **legacy**
shapes served by the deprecated unversioned routes (``/api/parse``,
``/api/standardize``).  They are preserved unchanged so existing callers
continue to work during the deprecation window.

Models with a ``V1`` suffix are the canonical v1 API contract served at
``/api/v1/``.  Field names here use geography-neutral terminology
(``region``, ``postal_code``) and responses carry ``api_version``.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Shared / utility
# ---------------------------------------------------------------------------

# ISO 3166-1 alpha-2 codes currently supported by this service.
# Extend as non-US parsing is added in future versions.
_SUPPORTED_COUNTRIES: frozenset[str] = frozenset({"US"})

# Full set of valid ISO 3166-1 alpha-2 codes (static list to avoid a
# heavy dependency).  Source: https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2
_VALID_ISO2: frozenset[str] = frozenset({
    "AD","AE","AF","AG","AI","AL","AM","AO","AQ","AR","AS","AT","AU","AW",
    "AX","AZ","BA","BB","BD","BE","BF","BG","BH","BI","BJ","BL","BM","BN",
    "BO","BQ","BR","BS","BT","BV","BW","BY","BZ","CA","CC","CD","CF","CG",
    "CH","CI","CK","CL","CM","CN","CO","CR","CU","CV","CW","CX","CY","CZ",
    "DE","DJ","DK","DM","DO","DZ","EC","EE","EG","EH","ER","ES","ET","FI",
    "FJ","FK","FM","FO","FR","GA","GB","GD","GE","GF","GG","GH","GI","GL",
    "GM","GN","GP","GQ","GR","GS","GT","GU","GW","GY","HK","HM","HN","HR",
    "HT","HU","ID","IE","IL","IM","IN","IO","IQ","IR","IS","IT","JE","JM",
    "JO","JP","KE","KG","KH","KI","KM","KN","KP","KR","KW","KY","KZ","LA",
    "LB","LC","LI","LK","LR","LS","LT","LU","LV","LY","MA","MC","MD","ME",
    "MF","MG","MH","MK","ML","MM","MN","MO","MP","MQ","MR","MS","MT","MU",
    "MV","MW","MX","MY","MZ","NA","NC","NE","NF","NG","NI","NL","NO","NP",
    "NR","NU","NZ","OM","PA","PE","PF","PG","PH","PK","PL","PM","PN","PR",
    "PS","PT","PW","PY","QA","RE","RO","RS","RU","RW","SA","SB","SC","SD",
    "SE","SG","SH","SI","SJ","SK","SL","SM","SN","SO","SR","SS","ST","SV",
    "SX","SY","SZ","TC","TD","TF","TG","TH","TJ","TK","TL","TM","TN","TO",
    "TR","TT","TV","TW","TZ","UA","UG","UM","US","UY","UZ","VA","VC","VE",
    "VG","VI","VN","VU","WF","WS","YE","YT","ZA","ZM","ZW",
})


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


# Convenience constant used by services.
USPS_PUB28_SPEC = ComponentSet(
    spec="usps-pub28",
    spec_version="unknown",
    values={},
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
    api_version: str = Field(default="1", description="API version that produced this error.")


# ---------------------------------------------------------------------------
# Request models (shared across v1 routes)
# ---------------------------------------------------------------------------

_COUNTRY_FIELD = Field(
    default="US",
    min_length=2,
    max_length=2,
    description="ISO 3166-1 alpha-2 country code. Only 'US' is supported in v1.",
    examples=["US"],
)


def _normalise_country(v: object) -> str:
    if isinstance(v, str):
        return v.strip().upper()
    return v  # type: ignore[return-value]


class ParseRequestV1(BaseModel):
    address: str = Field(..., max_length=1000)
    country: str = _COUNTRY_FIELD

    @field_validator("country", mode="before")
    @classmethod
    def normalise_country(cls, v: object) -> str:
        return _normalise_country(v)


class StandardizeRequestV1(BaseModel):
    """Accept either a raw address string *or* pre-parsed components.

    When both ``address`` and ``components`` are provided, ``components``
    takes precedence and ``address`` is ignored.
    """

    address: Optional[str] = Field(None, max_length=1000)
    components: Optional[dict[str, str]] = None
    country: str = _COUNTRY_FIELD

    @field_validator("country", mode="before")
    @classmethod
    def normalise_country(cls, v: object) -> str:
        return _normalise_country(v)


# ---------------------------------------------------------------------------
# Response models — v1
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    api_version: str = "1"


class ParseResponseV1(BaseModel):
    input: str
    country: str
    components: ComponentSet
    type: str
    warning: Optional[str] = None
    api_version: str = "1"


class StandardizeResponseV1(BaseModel):
    address_line_1: str
    address_line_2: str
    city: str
    region: str
    postal_code: str
    country: str
    standardized: str
    components: ComponentSet
    api_version: str = "1"


# ---------------------------------------------------------------------------
# Response models — legacy (deprecated unversioned routes)
# ---------------------------------------------------------------------------

class ParseRequest(BaseModel):
    address: str = Field(..., max_length=1000)


class ParseResponse(BaseModel):
    input: str
    components: dict[str, str]
    type: str
    warning: Optional[str] = None


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
