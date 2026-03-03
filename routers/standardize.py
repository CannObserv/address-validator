"""Deprecated standardize endpoint — preserved for backward compatibility.

This route will be removed after a suitable deprecation window.
Use ``POST /api/v1/standardize`` instead.
"""

from fastapi import APIRouter, Depends, HTTPException, Response

from auth import require_api_key
from models import StandardizeRequest, StandardizeResponse
from services.parser import parse_address_legacy
from services.standardizer import standardize_legacy

_DEPRECATION_LINK = '</api/v1/standardize>; rel="successor-version"'

router = APIRouter(
    prefix="/api",
    tags=["deprecated"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/standardize",
    response_model=StandardizeResponse,
    deprecated=True,
    summary="[DEPRECATED] Standardize address — use /api/v1/standardize",
)
def standardize_address(req: StandardizeRequest, response: Response) -> StandardizeResponse:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = _DEPRECATION_LINK

    if req.components is not None and len(req.components) > 0:
        comps = req.components
    elif req.address is not None:
        raw = req.address.strip()
        if not raw:
            raise HTTPException(
                status_code=400,
                detail="Provide 'address' (non-empty string) or 'components' (non-empty object).",
            )
        comps = parse_address_legacy(raw).components
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'address' (non-empty string) or 'components' (non-empty object).",
        )
    return standardize_legacy(comps)
