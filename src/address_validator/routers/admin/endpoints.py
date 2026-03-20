"""Per-endpoint detail view."""

import math

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import get_admin_user
from address_validator.routers.admin.queries import get_audit_rows, get_endpoint_stats
from address_validator.services.validation import cache_db

router = APIRouter(prefix="/endpoints")

_VALID_ENDPOINTS = {"parse", "standardize", "validate", "health"}
_PER_PAGE = 50


@router.get("/{name}", response_class=HTMLResponse, response_model=None)
async def endpoint_detail(
    request: Request,
    name: str,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
) -> Response:
    if name not in _VALID_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Unknown endpoint")

    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user

    try:
        engine = await cache_db.get_engine()
        stats = await get_endpoint_stats(engine, name)
        rows, total = await get_audit_rows(
            engine,
            page=page,
            per_page=_PER_PAGE,
            endpoint=name,
            client_ip=client_ip,
        )
    except Exception:
        stats = {}
        rows, total = [], 0

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip}

    # HTMX partial — return just the rows
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/endpoints/detail.html",
        {
            "request": request,
            "user": user,
            "active_nav": f"endpoint_{name}",
            "css_version": get_css_version(),
            "endpoint_name": name,
            "stats": stats,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
