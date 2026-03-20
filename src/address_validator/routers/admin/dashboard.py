"""Admin dashboard landing page."""

import subprocess

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from address_validator.routers.admin.deps import get_admin_user
from address_validator.routers.admin.queries import get_dashboard_stats
from address_validator.services.validation import cache_db, factory

templates = Jinja2Templates(directory="src/address_validator/templates")

router = APIRouter()

try:
    _css_version = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        text=True,
    ).strip()
except Exception:
    _css_version = "dev"


def _get_quota_info() -> list[dict]:
    """Read current quota state from provider singletons."""
    quota = []
    usps = factory._usps_provider  # noqa: SLF001
    if usps and hasattr(usps, "_client") and hasattr(usps._client, "_rate_limiter"):  # noqa: SLF001
        guard = usps._client._rate_limiter  # noqa: SLF001
        if len(guard._windows) > 1:  # noqa: SLF001
            quota.append(
                {
                    "provider": "usps",
                    "remaining": int(guard._tokens[1]),  # noqa: SLF001
                    "limit": guard._windows[1].limit,  # noqa: SLF001
                }
            )
    google = factory._google_provider  # noqa: SLF001
    if google and hasattr(google, "_client") and hasattr(google._client, "_rate_limiter"):  # noqa: SLF001
        guard = google._client._rate_limiter  # noqa: SLF001
        if len(guard._windows) > 1:  # noqa: SLF001
            quota.append(
                {
                    "provider": "google",
                    "remaining": int(guard._tokens[1]),  # noqa: SLF001
                    "limit": guard._windows[1].limit,  # noqa: SLF001
                }
            )
    return quota


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
            "css_version": _css_version,
            "stats": stats,
            "quota": _get_quota_info(),
        },
    )
