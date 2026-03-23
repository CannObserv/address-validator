"""Per-provider detail view."""

import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_audit_rows, get_provider_stats

router = APIRouter(prefix="/providers")

_VALID_PROVIDERS = {"usps", "google"}
_PER_PAGE = 50


@router.get("/{name}", response_class=HTMLResponse, response_model=None)
async def provider_detail(
    request: Request,
    name: str,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    if name not in _VALID_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    stats = await get_provider_stats(ctx.engine, name)
    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        provider=name,
        client_ip=client_ip,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip}

    # Find quota for this provider
    quota = None
    for q in get_quota_info(ctx.request):
        if q["provider"] == name:
            quota = q
            break

    # HTMX partial — return just the rows (skip for boosted nav)
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/providers/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": f"provider_{name}",
            "css_version": get_css_version(),
            "provider_name": name,
            "stats": stats,
            "quota": quota,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
