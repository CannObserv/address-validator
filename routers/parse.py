"""Parse endpoint: break a raw address string into labelled components."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.parser import parse_address as do_parse

router = APIRouter(prefix="/api", tags=["parse"])


class ParseRequest(BaseModel):
    address: str


class ParseResponse(BaseModel):
    input: str
    components: dict[str, str]
    type: str
    warning: Optional[str] = None


@router.post("/parse", response_model=ParseResponse)
def parse_address(req: ParseRequest) -> ParseResponse:
    raw = req.address.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="address is required")
    return ParseResponse(**do_parse(raw))
