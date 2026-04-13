"""Admin HTMX partials — small lazy-load fragments injected into the nav and
shared layout. These endpoints are not user-navigable pages; they exist so
that templates rendered by *every* admin handler don't have to fetch
data they only need for one nav element.

Routes here live under `/admin/_partials/` to keep them clearly separated
from page routes. Adding a new fragment? Put it in this module.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_new_candidate_count

router = APIRouter(prefix="/_partials")

# Window the badge counts over. Matches the candidate list view's default
# `since=30d` filter — keep these two in sync if either changes.
_CANDIDATES_BADGE_LOOKBACK_DAYS = 30


@router.get("/candidates_badge", response_class=HTMLResponse, response_model=None)
async def candidates_badge(
    request: Request,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    """HTMX-loaded fragment shown next to the Candidates nav link.

    Counts groups (rollup status `new` or `mixed`) over the lookback window.
    Intentionally *not* filtered by `failure_type` — the badge is a global
    triage-queue indicator, not a filter-aware view.
    """
    since = datetime.now(UTC) - timedelta(days=_CANDIDATES_BADGE_LOOKBACK_DAYS)
    count = await get_new_candidate_count(ctx.engine, since=since)
    return templates.TemplateResponse(
        "admin/candidates/_badge.html",
        {"request": request, "count": count},
    )
