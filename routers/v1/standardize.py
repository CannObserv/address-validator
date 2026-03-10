"""v1 standardize endpoint."""

from fastapi import APIRouter, Depends

from auth import require_api_key
from models import ErrorResponse, StandardizeRequestV1, StandardizeResponseV1
from routers.v1.core import APIError, check_country
from services.parser import parse_address
from services.standardizer import standardize

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

    return standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
