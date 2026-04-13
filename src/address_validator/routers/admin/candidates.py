"""Admin candidate-triage views — browse, triage, and annotate training candidates."""

import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_candidate_groups

router = APIRouter(prefix="/candidates")

_PER_PAGE = 50
_VALID_FAILURE_TYPES = {"repeated_label_error", "post_parse_recovery"}
_VALID_STATUSES = {"new", "reviewed", "rejected", "all"}
_VALID_WRITE_STATUSES = {"new", "reviewed", "rejected"}


def _parse_since(raw: str | None) -> datetime | None:
    """Parse a `--since` querystring: '7d', '30d', '90d', 'all', or ISO date."""
    if not raw or raw == "all":
        return None
    try:
        if raw.endswith("d"):
            return datetime.now(UTC) - timedelta(days=int(raw[:-1]))
        if raw.endswith("h"):
            return datetime.now(UTC) - timedelta(hours=int(raw[:-1]))
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse, response_model=None)
async def candidates_list(
    request: Request,
    page: int = Query(1, ge=1),
    status: str = Query("new"),
    failure_type: str | None = Query(None),
    since: str = Query("30d"),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    if status not in _VALID_STATUSES:
        status = "new"
    if failure_type not in (None, "") and failure_type not in _VALID_FAILURE_TYPES:
        failure_type = None
    if not failure_type:
        failure_type = None
    since_dt = _parse_since(since)

    query_status = None if status == "all" else status

    rows, total = await get_candidate_groups(
        ctx.engine,
        status=query_status,
        failure_type=failure_type,
        since=since_dt,
        until=None,
        limit=_PER_PAGE,
        offset=(page - 1) * _PER_PAGE,
    )
    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"status": status, "failure_type": failure_type, "since": since}

    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/candidates/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/candidates/index.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "candidates",
            "css_version": get_css_version(),
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
