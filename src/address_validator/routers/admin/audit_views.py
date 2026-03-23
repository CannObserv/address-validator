"""Audit log view — paginated, filterable audit trail."""

import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_audit_rows

router = APIRouter(prefix="/audit")

_PER_PAGE = 50


@router.get("/", response_class=HTMLResponse, response_model=None)
async def audit_list(
    request: Request,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    endpoint: str | None = Query(None),
    status_min: int | None = Query(None, ge=100, le=599),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        endpoint=endpoint,
        client_ip=client_ip,
        status_min=status_min,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip, "endpoint": endpoint, "status_min": status_min}

    # HTMX partial — return just the rows (skip for boosted nav)
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/audit/list.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "audit",
            "css_version": get_css_version(),
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
