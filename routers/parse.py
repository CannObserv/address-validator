"""Parse endpoint: break a raw address string into labelled components."""

from fastapi import APIRouter, Depends, HTTPException

from auth import require_api_key
from models import ParseRequest, ParseResponse
from services.parser import parse_address as do_parse

router = APIRouter(prefix="/api", tags=["parse"], dependencies=[Depends(require_api_key)])


@router.post("/parse", response_model=ParseResponse)
def parse_address(req: ParseRequest) -> ParseResponse:
    raw = req.address.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="address is required")
    return do_parse(raw)
