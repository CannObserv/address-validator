"""v1 standardize endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from auth import require_api_key
from models import (
    ErrorResponse,
    StandardizeRequestV1,
    StandardizeResponseV1,
    _SUPPORTED_COUNTRIES,
    _VALID_ISO2,
)
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
def standardize_address_v1(
    req: StandardizeRequestV1, response: Response
) -> StandardizeResponseV1:
    response.headers["API-Version"] = "1"

    country = req.country
    if country not in _VALID_ISO2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                error="invalid_country_code",
                message=f"'{country}' is not a valid ISO 3166-1 alpha-2 country code.",
            ).model_dump(),
        )
    if country not in _SUPPORTED_COUNTRIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                error="country_not_supported",
                message=f"Country '{country}' is not yet supported. Currently supported: US.",
            ).model_dump(),
        )

    if req.components is not None and len(req.components) > 0:
        comps = req.components
    elif req.address is not None:
        raw = req.address.strip()
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="address_required",
                    message="Provide 'address' (non-empty string) or 'components' (non-empty object).",
                ).model_dump(),
            )
        comps = parse_address(raw, country=country).components.values
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="components_or_address_required",
                message="Provide 'address' (non-empty string) or 'components' (non-empty object).",
            ).model_dump(),
        )

    return standardize(comps, country=country)
