"""Parse endpoint: break a raw address string into labelled components."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.parser import parse_address

router = APIRouter(prefix="/api", tags=["parse"])


class ParseRequest(BaseModel):
    address: str


@router.post("/parse")
def parse(req: ParseRequest):
    raw = req.address.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="address is required")
    return parse_address(raw)
