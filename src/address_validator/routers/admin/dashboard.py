"""Admin dashboard landing page."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin._sparkline import SPARKLINE_CONFIG, build_sparkline_svg
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import (
    get_dashboard_stats,
    get_provider_daily_usage,
    get_sparkline_data,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse, response_model=None)
async def admin_dashboard(ctx: AdminContext = Depends(get_admin_context)) -> Response:
    stats = await get_dashboard_stats(ctx.engine)
    sparkline_points = await get_sparkline_data(ctx.engine)
    sparkline_svgs = {
        key: build_sparkline_svg(
            sparkline_points.get(key, []),
            color=color,
            label=label,
        )
        for key, (color, label) in SPARKLINE_CONFIG.items()
    }
    daily_usage = await get_provider_daily_usage(ctx.engine)
    quota = [
        {**q, "requests_today": daily_usage.get(q["provider"], 0)}
        for q in get_quota_info(ctx.request)
    ]
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": ctx.request,
            "user": ctx.user,
            "active_nav": "dashboard",
            "css_version": get_css_version(),
            "stats": stats,
            "quota": quota,
            "sparkline_svgs": sparkline_svgs,
        },
    )
