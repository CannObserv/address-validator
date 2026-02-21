"""Standardize endpoint: normalise address per USPS Pub 28."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.parser import parse_address
from services.standardizer import standardize

router = APIRouter(prefix="/api", tags=["standardize"])


class StandardizeRequest(BaseModel):
    """Accept either a raw address string *or* pre-parsed components.

    When both ``address`` and ``components`` are provided, ``components``
    takes precedence and ``address`` is ignored.
    """
    address: Optional[str] = None
    components: Optional[dict[str, str]] = None


class StandardizeResponse(BaseModel):
    address_line_1: str
    address_line_2: str
    city: str
    state: str
    zip_code: str
    standardized: str
    components: dict[str, str]


@router.post("/standardize", response_model=StandardizeResponse)
def standardize_address(req: StandardizeRequest) -> StandardizeResponse:
    if req.components is not None and len(req.components) > 0:
        comps = req.components
    elif req.address is not None and req.address.strip():
        comps = parse_address(req.address.strip())["components"]
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'address' (non-empty string) or 'components' (non-empty object).",
        )
    return StandardizeResponse(**standardize(comps))
