"""v1 standardize endpoint."""

from fastapi import APIRouter, Depends

from address_validator.auth import require_api_key
from address_validator.models import ErrorResponse, StandardizeRequestV1, StandardizeResponseV1
from address_validator.routers.v1.core import check_country
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize

router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/standardize",
    response_model=StandardizeResponseV1,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def standardize_address_v1(req: StandardizeRequestV1) -> StandardizeResponseV1:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        comps = req.components
    else:
        # model_validator guarantees address is non-blank when components is absent
        parse_result = parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings

    return standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
