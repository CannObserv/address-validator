"""v1 validate endpoint.

POST /api/v1/validate — parses and standardizes the input address, then
confirms it represents a real deliverable location by delegating to the
configured :class:`~services.validation.protocol.ValidationProvider`.

Input pipeline
--------------
Both input modes run through the same parse → standardize pipeline before
the provider is called.  This guarantees that providers always receive
clean, USPS-formatted components regardless of how the caller supplied the
address.

* **Raw address string** (``address`` field): the string is parsed by
  :func:`~services.parser.parse_address` and then standardized by
  :func:`~services.standardizer.standardize`.
* **Pre-parsed components** (``components`` field): the dict is passed
  directly to :func:`~services.standardizer.standardize`, skipping the
  parse step.

When both fields are supplied, ``components`` takes precedence and
``address`` is ignored.

Warnings emitted by the parse or standardize step are merged into the
``warnings`` list of the final response alongside any warnings from the
provider itself.

The active provider is controlled by the ``VALIDATION_PROVIDER`` env var
(see :mod:`services.validation.config`).  When no provider is configured
the endpoint still returns HTTP 200 with ``validation.status='unavailable'``
so upstream callers degrade gracefully.
"""

import logging
import math

from fastapi import APIRouter, Depends, Request

from address_validator.auth import require_api_key
from address_validator.models import ErrorResponse, ValidateRequestV1, ValidateResponseV1
from address_validator.routers.v1.core import APIError, check_country
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize
from address_validator.services.validation.errors import ProviderRateLimitedError

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
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
    summary="Validate an address against an authoritative source",
    description=(
        "Parses, standardizes, and then confirms that an address represents a "
        "real USPS deliverable location.\n\n"
        "**Input modes** (both run through parse → standardize before validation):\n"
        "- `address` — raw address string; parsed then standardized automatically.\n"
        "- `components` — pre-parsed component dict; standardized only (parse skipped).\n"
        "When both are supplied, `components` takes precedence.\n\n"
        "**DPV match codes**\n"
        "- `Y` — confirmed delivery point\n"
        "- `S` — building confirmed, secondary address (apt/unit) missing\n"
        "- `D` — building confirmed, secondary address not recognised\n"
        "- `N` — address not found\n\n"
        "When no validation provider is configured, `validation.status` is "
        "`unavailable` and all other result fields are `null`.\n\n"
        "HTTP 429 is returned when all configured providers are currently "
        "rate-limited and no further fallbacks are available. "
        "The response includes a `Retry-After` header indicating the "
        "recommended number of seconds to wait before retrying."
    ),
)
async def validate_address_v1(req: ValidateRequestV1, request: Request) -> ValidateResponseV1:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        comps = req.components
    elif req.address is not None:
        raw = req.address.strip()
        if not raw:
            raise APIError(
                status_code=400,
                error="address_required",
                message="Provide 'address' (non-empty string) or 'components' (non-empty object).",
            )
        parse_result = parse_address(raw, country=req.country)
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings
    else:
        raise APIError(
            status_code=400,
            error="components_or_address_required",
            message="Provide 'address' (non-empty string) or 'components' (non-empty object).",
        )

    std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)

    provider = request.app.state.registry.get_provider()
    logger.debug("validate_address_v1: provider=%s", type(provider).__name__)
    try:
        result = await provider.validate(std)
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
