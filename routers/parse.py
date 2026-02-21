"""Parse endpoint: break a raw address string into labelled components."""

from fastapi import APIRouter, HTTPException

from models import ParseRequest, ParseResponse
from services.parser import parse_address as do_parse

router = APIRouter(prefix="/api", tags=["parse"])


@router.post("/parse", response_model=ParseResponse)
def parse_address(req: ParseRequest) -> ParseResponse:
    raw = req.address.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="address is required")
    return do_parse(raw)
