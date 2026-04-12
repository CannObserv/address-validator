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
Non-US validation requires ``VALIDATION_PROVIDER=google`` or any chain
containing a Google provider (e.g. ``usps,google``).
"""

import logging
import math

from fastapi import APIRouter, Depends

from address_validator.auth import require_api_key
from address_validator.core.errors import APIError
from address_validator.models import (
    ErrorResponse,
    StandardizeResponseV1,
    ValidateRequestV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.routers.deps import get_registry
from address_validator.routers.v1.core import check_country
from address_validator.services.audit import set_audit_context
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)
from address_validator.services.validation.pipeline import (
    run_non_us_pipeline_v1,
    run_us_pipeline,
)
from address_validator.services.validation.registry import ProviderRegistry

logger = logging.getLogger(__name__)

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
async def validate_address_v1(
    req: ValidateRequestV1,
    registry: ProviderRegistry = Depends(get_registry),
) -> ValidateResponseV1:
    if req.country != "US":
        std, raw_input, provider = await run_non_us_pipeline_v1(req, registry)
    else:
        check_country(req.country)
        std, raw_input, provider = await run_us_pipeline(
            req, registry, component_profile="usps-pub28"
        )

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


# Expose StandardizeResponseV1 for callers that imported it from this module
# in older code paths (backward-compat shim).
__all__ = ["StandardizeResponseV1", "router", "validate_address_v1"]
