"""Standardize endpoint: normalise address per USPS Pub 28."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.parser import parse_address
from services.standardizer import standardize

router = APIRouter(prefix="/api", tags=["standardize"])


class StandardizeRequest(BaseModel):
    """Accept either a raw address string *or* pre-parsed components."""
    address: Optional[str] = None
    components: Optional[dict[str, str]] = None


@router.post("/standardize")
def standardize_address(req: StandardizeRequest):
    if req.components:
        comps = req.components
    elif req.address:
        parsed = parse_address(req.address.strip())
        comps = parsed["components"]
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'address' (string) or 'components' (object).",
        )
    return standardize(comps)
