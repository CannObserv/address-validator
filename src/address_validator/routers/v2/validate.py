"""v2 validate endpoint.

POST /api/v2/validate — parses, standardizes, and validates an address
against an authoritative source.

Input pipeline
--------------
**US addresses** run through the full parse → standardize pipeline before
the provider is called.  Providers always receive clean, USPS-formatted
components.

* **Raw address string** (``address`` field): parsed by
  :func:`~services.parser.parse_address` then standardized.
* **Pre-parsed components** (``components`` field): passed directly to
  :func:`~services.standardizer.standardize`, skipping the parse step.

**Non-US addresses** must supply pre-parsed ``components``, except for
CA which supports raw address strings via the libpostal sidecar.  The USPS
pipeline is bypassed; for CA, components run through ``_standardize_ca()``;
other non-US components are passed verbatim to the Google provider.
Non-CA non-US raw address strings are rejected with 422 ``country_not_supported``.

When both ``address`` and ``components`` are supplied, ``components``
takes precedence and ``address`` is ignored.

Warnings from the parse or standardize step are merged into the
``warnings`` list of the final response alongside any provider warnings.

The active provider is controlled by the ``VALIDATION_PROVIDER`` env var
(see :mod:`services.validation.config`).  When no provider is configured
the endpoint returns HTTP 200 with ``validation.status='unavailable'``.
Non-US validation requires ``VALIDATION_PROVIDER=google`` or any chain
containing a Google provider (e.g. ``usps,google``).

The ``component_profile`` query parameter selects the component key
vocabulary in the response.  It is validated but does not affect the
validate response structure — provider components are returned as-is.
"""

import json
import logging
import math

from fastapi import APIRouter, Depends, Query, Request

from address_validator.auth import require_api_key
from address_validator.core.address_format import build_validated_string
from address_validator.models import (
    ComponentSet,
    ErrorResponse,
    StandardizeResponseV1,
    ValidateRequestV1,
    ValidateResponseV1,
    ValidateResponseV2,
    ValidationResult,
)
from address_validator.routers.v1.core import VALID_ISO2, APIError, check_country
from address_validator.services.audit import set_audit_context
from address_validator.services.component_profiles import (
    VALID_PROFILES,
    translate_components_to_iso,
)
from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)

logger = logging.getLogger(__name__)

_COMPONENT_PROFILE_DESCRIPTION = (
    "Component key vocabulary. "
    "`iso-19160-4` (default): ISO 19160-4 element names. "
    "`usps-pub28`: USPS Publication 28 snake_case names (v1 backward compat). "
    "`canada-post`: reserved; currently identical to `iso-19160-4`."
)


def _build_non_us_std(components: dict[str, str], country: str) -> StandardizeResponseV1:
    """Build a passthrough StandardizeResponseV1 from raw components for non-US addresses.

    Skips the USPS Pub 28 pipeline entirely.  Components are used verbatim.
    The ``components.spec`` is ``"raw"`` to indicate no standardization was applied.
    """
    address_line_1 = components.get("address_line_1", "")
    address_line_2 = components.get("address_line_2", "")
    city = components.get("city", "")
    region = components.get("region", "")
    postal_code = components.get("postal_code", "")
    standardized = build_validated_string(address_line_1, address_line_2, city, region, postal_code)
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=standardized,
        components=ComponentSet(spec="raw", spec_version="1", values=components),
    )


async def _setup_non_us_validate(
    req: "ValidateRequestV1",
    request: "Request",
) -> "tuple[StandardizeResponseV1, str | None, object]":
    """Validate country, provider capability, and build std for non-US addresses.

    Returns ``(std, raw_input, provider)``.
    Raises ``APIError`` (422 or 503) on validation failures.
    """
    if req.country not in VALID_ISO2:
        raise APIError(
            status_code=422,
            error="invalid_country_code",
            message=f"'{req.country}' is not a valid ISO 3166-1 alpha-2 country code.",
        )
    if not req.components and req.country != "CA":
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Raw address strings are only supported for US and CA. "
                "Supply pre-parsed 'components' for other countries."
            ),
        )
    provider = request.app.state.registry.get_provider()
    if not provider.supports_non_us:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Non-US address validation requires the Google provider. "
                "Set VALIDATION_PROVIDER=google or VALIDATION_PROVIDER=usps,google."
            ),
        )
    if req.components:
        std: StandardizeResponseV1 = _build_non_us_std(req.components, req.country)
        raw_input: str | None = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
    else:
        # CA raw string: parse via libpostal then CA standardize
        libpostal_client = getattr(request.app.state, "libpostal_client", None)
        try:
            parse_result = await parse_address(  # type: ignore[union-attr]
                req.address.strip(), country="CA", libpostal_client=libpostal_client
            )
        except LibpostalUnavailableError as exc:
            raise APIError(
                status_code=503,
                error="parsing_unavailable",
                message=(
                    "CA address parsing is currently unavailable. Provide pre-parsed components."
                ),
            ) from exc
        std = standardize(
            parse_result.components.values, country="CA", upstream_warnings=parse_result.warnings
        )
        raw_input = req.address
    return std, raw_input, provider


def _v1_to_v2(v1: ValidateResponseV1) -> ValidateResponseV2:
    """Convert a ValidateResponseV1 to ValidateResponseV2.

    V2 drops latitude/longitude and uses empty strings (not None) for address fields.
    """
    return ValidateResponseV2(
        address_line_1=v1.address_line_1 or "",
        address_line_2=v1.address_line_2 or "",
        city=v1.city or "",
        region=v1.region or "",
        postal_code=v1.postal_code or "",
        country=v1.country,
        validated=v1.validated,
        validation=v1.validation,
        components=v1.components,
        warnings=v1.warnings,
    )


router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/validate",
    response_model=ValidateResponseV2,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Validate an address against an authoritative source",
    description=(
        "Parses and validates an address against an authoritative source.\n\n"
        "**US addresses** run through the full parse → standardize pipeline "
        "before validation. Both input modes are supported:\n"
        "- `address` — raw address string; parsed then standardized automatically.\n"
        "- `components` — pre-parsed component dict; standardized only (parse skipped).\n"
        "When both are supplied, `components` takes precedence.\n\n"
        "**Non-US addresses:** CA supports raw strings via libpostal; other countries "
        "require pre-parsed `components` (raw strings → 422 `country_not_supported`). "
        "Requires `VALIDATION_PROVIDER=google` or a chain containing Google"
        " (e.g. `usps,google`).\n\n"
        "**US DPV match codes** (in `validation.dpv_match_code`):\n"
        "- `Y` — confirmed delivery point\n"
        "- `S` — building confirmed, secondary address (apt/unit) missing\n"
        "- `D` — building confirmed, secondary address not recognised\n"
        "- `N` — address not found\n\n"
        "**Non-US validation statuses** (no DPV codes):\n"
        "- `confirmed` — address complete and geocoded\n"
        "- `invalid` — geocodable but incomplete (e.g. missing street number)\n"
        "- `not_found` — address could not be geocoded or verified\n\n"
        "When no validation provider is configured, `validation.status` is "
        "`unavailable` and all other result fields are `null`.\n\n"
        "When the validation provider rejects the input as malformed, "
        "`validation.status` is `error`.\n\n"
        "HTTP 429 is returned when all configured providers are currently "
        "rate-limited and no further fallbacks are available. "
        "The response includes a `Retry-After` header indicating the "
        "recommended number of seconds to wait before retrying.\n\n"
        "The `component_profile` query parameter selects the component key "
        "vocabulary (`iso-19160-4` default, `usps-pub28` for v1 compat). "
        "It is validated but does not affect the validate response structure."
    ),
)
async def validate_address_v2(
    req: ValidateRequestV1,
    request: Request,
    component_profile: str = Query(
        default="iso-19160-4",
        description=_COMPONENT_PROFILE_DESCRIPTION,
    ),
) -> ValidateResponseV2:
    if component_profile not in VALID_PROFILES:
        raise APIError(
            status_code=422,
            error="invalid_component_profile",
            message=(
                f"Unknown component_profile '{component_profile}'. "
                f"Valid values: {sorted(VALID_PROFILES)}."
            ),
        )

    if req.country != "US":
        std, raw_input, provider = await _setup_non_us_validate(req, request)
    else:
        check_country(req.country)

        upstream_warnings: list[str] = []

        if req.components:
            comps = translate_components_to_iso(req.components, component_profile)
            raw_input = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
        else:
            # model_validator guarantees address is non-blank when components is absent
            parse_result = await parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
            comps = parse_result.components.values
            upstream_warnings = parse_result.warnings
            raw_input = req.address

        std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
        provider = request.app.state.registry.get_provider()
    logger.debug("validate_address_v2: provider=%s", type(provider).__name__)
    try:
        v1_result = await provider.validate(std, raw_input=raw_input)
    except ProviderBadRequestError as exc:
        logger.warning("Validation provider %s rejected request", exc.provider)
        set_audit_context(provider=exc.provider, validation_status="error", cache_hit=False)
        warnings = ["Validation provider rejected the address as malformed"]
        result = ValidateResponseV2(
            country=std.country,
            validation=ValidationResult(status="error", provider=exc.provider),
            warnings=std.warnings + warnings,
        )
        return result
    except ProviderRateLimitedError as exc:
        raise APIError(
            status_code=429,
            error="provider_rate_limited",
            message="All configured validation providers are currently rate-limited. Retry later.",
            headers={"Retry-After": str(math.ceil(exc.retry_after_seconds))},
        ) from None

    result = _v1_to_v2(v1_result)
    if std.warnings:
        result = result.model_copy(update={"warnings": std.warnings + result.warnings})

    return result
