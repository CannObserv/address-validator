"""Admin dashboard landing page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin.deps import get_admin_user
from address_validator.routers.admin.queries import get_dashboard_stats
from address_validator.services.validation import cache_db

router = APIRouter()


@router.get("/", response_class=HTMLResponse, response_model=None)
async def admin_dashboard(request: Request) -> Response:
    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user
    try:
        engine = await cache_db.get_engine()
        stats = await get_dashboard_stats(engine)
    except Exception:
        stats = {}
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "active_nav": "dashboard",
            "css_version": get_css_version(),
            "stats": stats,
            "quota": get_quota_info(),
        },
    )
