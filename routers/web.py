"""Serve the web UI."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])

_HTML = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


@router.get("/", response_class=HTMLResponse)
def index():
    return _HTML
