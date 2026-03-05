"""v1 validate endpoint.

POST /api/v1/validate — confirms that an address represents a real
deliverable location by delegating to the configured
:class:`~services.validation.protocol.ValidationProvider`.

The active provider is controlled by the ``VALIDATION_PROVIDER`` env var
(see :mod:`services.validation.factory`).  When no provider is configured
the endpoint still returns HTTP 200 with ``validation_status='unavailable'``
so upstream callers degrade gracefully.
"""

import logging

from fastapi import APIRouter, Depends

from auth import require_api_key
from models import ErrorResponse, ValidateRequestV1, ValidateResponseV1
from routers.v1.core import APIError, check_country
from services.validation.factory import get_provider

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
    },
    summary="Validate an address against an authoritative source",
    description=(
        "Confirms that an address represents a real USPS deliverable location "
        "and returns corrected components plus a DPV match code.\n\n"
        "**DPV match codes**\n"
        "- `Y` — confirmed delivery point\n"
        "- `S` — building confirmed, secondary address (apt/unit) missing\n"
        "- `D` — building confirmed, secondary address not recognised\n"
        "- `N` — address not found\n\n"
        "When no validation provider is configured, `validation_status` is "
        "`unavailable` and all other result fields are `null`."
    ),
)
async def validate_address_v1(req: ValidateRequestV1) -> ValidateResponseV1:
    check_country(req.country)

    if not req.address.strip():
        raise APIError(
            status_code=400,
            error="address_required",
            message="address is required and must not be blank.",
        )

    provider = get_provider()
    logger.debug("validate_address_v1: provider=%s", type(provider).__name__)
    return await provider.validate(req)
