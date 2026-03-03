"""Deprecated parse endpoint — preserved for backward compatibility.

This route will be removed after a suitable deprecation window.
Use ``POST /api/v1/parse`` instead.
"""

from fastapi import APIRouter, Depends, HTTPException, Response

from auth import require_api_key
from models import ParseRequest, ParseResponse
from services.parser import parse_address_legacy

_DEPRECATION_LINK = '</api/v1/parse>; rel="successor-version"'

router = APIRouter(
    prefix="/api",
    tags=["deprecated"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/parse",
    response_model=ParseResponse,
    deprecated=True,
    summary="[DEPRECATED] Parse address — use /api/v1/parse",
)
def parse_address(req: ParseRequest, response: Response) -> ParseResponse:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = _DEPRECATION_LINK

    raw = req.address.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="address is required")
    return parse_address_legacy(raw)
