"""Audit log view — paginated, filterable audit trail."""

import math

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from address_validator.routers.admin.dashboard import _css_version
from address_validator.routers.admin.deps import get_admin_user
from address_validator.routers.admin.queries import get_audit_rows
from address_validator.services.validation import cache_db

templates = Jinja2Templates(directory="src/address_validator/templates")

router = APIRouter(prefix="/audit")

_PER_PAGE = 50


@router.get("/", response_class=HTMLResponse, response_model=None)
async def audit_list(
    request: Request,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    endpoint: str | None = Query(None),
    status_min: int | None = Query(None, ge=100, le=599),
) -> Response:
    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user

    try:
        engine = await cache_db.get_engine()
        rows, total = await get_audit_rows(
            engine,
            page=page,
            per_page=_PER_PAGE,
            endpoint=endpoint,
            client_ip=client_ip,
            status_min=status_min,
        )
    except Exception:
        rows, total = [], 0

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip, "endpoint": endpoint, "status_min": status_min}

    # HTMX partial — return just the rows
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/audit/list.html",
        {
            "request": request,
            "user": user,
            "active_nav": "audit",
            "css_version": _css_version,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
