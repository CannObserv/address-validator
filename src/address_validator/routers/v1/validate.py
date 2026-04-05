"""v1 validate endpoint.

POST /api/v1/validate — parses, standardizes, and validates an address
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

**Non-US addresses** must supply pre-parsed ``components``.  The USPS
pipeline is bypassed entirely; components are passed verbatim to the
Google provider.  Non-US raw address strings are rejected with 422
``country_not_supported``.

When both ``address`` and ``components`` are supplied, ``components``
takes precedence and ``address`` is ignored.

Warnings from the parse or standardize step are merged into the
``warnings`` list of the final response alongside any provider warnings.

The active provider is controlled by the ``VALIDATION_PROVIDER`` env var
(see :mod:`services.validation.config`).  When no provider is configured
the endpoint returns HTTP 200 with ``validation.status='unavailable'``.
Non-US validation requires a provider with ``supports_non_us=True`` —
``VALIDATION_PROVIDER=google`` or any chain containing a Google provider (e.g. ``usps,google``).
"""

import json
import logging
import math

from fastapi import APIRouter, Depends, Request

from address_validator.auth import require_api_key
from address_validator.core.address_format import build_validated_string
from address_validator.models import (
    ComponentSet,
    ErrorResponse,
    StandardizeResponseV1,
    ValidateRequestV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.routers.v1.core import VALID_ISO2, APIError, check_country
from address_validator.services.audit import set_audit_context
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)

logger = logging.getLogger(__name__)


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


router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/validate",
    response_model=ValidateResponseV1,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
    summary="Validate an address against an authoritative source",
    description=(
        "Parses and validates an address against an authoritative source.\n\n"
        "**US addresses** run through the full parse → standardize pipeline "
        "before validation. Both input modes are supported:\n"
        "- `address` — raw address string; parsed then standardized automatically.\n"
        "- `components` — pre-parsed component dict; standardized only (parse skipped).\n"
        "When both are supplied, `components` takes precedence.\n\n"
        "**Non-US addresses** must supply pre-parsed `components` "
        "(raw strings → 422 `country_not_supported`). "
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
        "recommended number of seconds to wait before retrying."
    ),
)
async def validate_address_v1(req: ValidateRequestV1, request: Request) -> ValidateResponseV1:
    if req.country != "US":
        if req.country not in VALID_ISO2:
            raise APIError(
                status_code=422,
                error="invalid_country_code",
                message=f"'{req.country}' is not a valid ISO 3166-1 alpha-2 country code.",
            )
        if not req.components:
            raise APIError(
                status_code=422,
                error="country_not_supported",
                message=(
                    "Raw address strings are only supported for US. "
                    "Supply pre-parsed 'components' for non-US addresses."
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
        std = _build_non_us_std(req.components, req.country)
        raw_input: str | None = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
    else:
        check_country(req.country)

        upstream_warnings: list[str] = []

        if req.components:
            comps = req.components
            raw_input = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
        else:
            # model_validator guarantees address is non-blank when components is absent
            parse_result = parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
            comps = parse_result.components.values
            upstream_warnings = parse_result.warnings
            raw_input = req.address

        std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
        provider = request.app.state.registry.get_provider()
    logger.debug("validate_address_v1: provider=%s", type(provider).__name__)
    try:
        result = await provider.validate(std, raw_input=raw_input)
    except ProviderBadRequestError as exc:
        logger.warning("Validation provider %s rejected request", exc.provider)
        set_audit_context(provider=exc.provider, validation_status="error", cache_hit=False)
        result = ValidateResponseV1(
            country=std.country,
            validation=ValidationResult(status="error", provider=exc.provider),
            warnings=["Validation provider rejected the address as malformed"],
        )
    except ProviderRateLimitedError as exc:
        raise APIError(
            status_code=429,
            error="provider_rate_limited",
            message="All configured validation providers are currently rate-limited. Retry later.",
            headers={"Retry-After": str(math.ceil(exc.retry_after_seconds))},
        ) from None

    if std.warnings:
        result = result.model_copy(update={"warnings": std.warnings + result.warnings})

    return result
