"""v1 parse endpoint."""

from fastapi import APIRouter, Depends

from address_validator.auth import require_api_key
from address_validator.models import ErrorResponse, ParseRequestV1, ParseResponseV1
from address_validator.routers.v1.core import APIError, check_country
from address_validator.services.parser import parse_address

router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/parse",
    response_model=ParseResponseV1,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def parse_address_v1(req: ParseRequestV1) -> ParseResponseV1:
    check_country(req.country)

    raw = req.address.strip()
    if not raw:
        raise APIError(
            status_code=400,
            error="address_required",
            message="address is required and must not be blank.",
        )

    return parse_address(raw, country=req.country)
