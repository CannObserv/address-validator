"""Standardize endpoint: normalise address per USPS Pub 28."""

from fastapi import APIRouter, HTTPException

from models import StandardizeRequest, StandardizeResponse
from services.parser import parse_address
from services.standardizer import standardize

router = APIRouter(prefix="/api", tags=["standardize"])


@router.post("/standardize", response_model=StandardizeResponse)
def standardize_address(req: StandardizeRequest) -> StandardizeResponse:
    if req.components is not None and len(req.components) > 0:
        comps = req.components
    elif req.address is not None:
        raw = req.address.strip()
        if not raw:
            raise HTTPException(
                status_code=400,
                detail="Provide 'address' (non-empty string) or 'components' (non-empty object).",
            )
        comps = parse_address(raw).components
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'address' (non-empty string) or 'components' (non-empty object).",
        )
    return standardize(comps)
