"""Admin candidate-triage views — browse, triage, and annotate training candidates."""

import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import (
    get_candidate_group,
    get_candidate_groups,
    get_candidate_submissions,
    update_candidate_notes,
    update_candidate_status,
)
from address_validator.routers.admin.queries.batches import (
    get_assignable_batches,
    get_batch_by_slug,
)
from address_validator.routers.admin.queries.candidates import (
    DEFAULT_LOOKBACK_DAYS,
    WRITE_STATUSES,
)
from address_validator.services.training_batches import (
    assign_candidates,
    unassign_candidates,
)

router = APIRouter(prefix="/candidates")

_PER_PAGE = 50
_VALID_FAILURE_TYPES: frozenset[str] = frozenset({"repeated_label_error", "post_parse_recovery"})
_VALID_STATUSES: frozenset[str] = WRITE_STATUSES | {"all", "assigned"}
_DEFAULT_SINCE = f"{DEFAULT_LOOKBACK_DAYS}d"


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
    since: str = Query(_DEFAULT_SINCE),
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


@router.get("/{raw_hash}", response_class=HTMLResponse, response_model=None)
async def candidates_detail(
    request: Request,
    raw_hash: str,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    group = await get_candidate_group(ctx.engine, raw_hash=raw_hash)
    if group is None:
        raise HTTPException(status_code=404, detail="candidate group not found")
    submissions = await get_candidate_submissions(ctx.engine, raw_hash=raw_hash)
    assignable = await get_assignable_batches(ctx.engine)
    return templates.TemplateResponse(
        "admin/candidates/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "candidates",
            "css_version": get_css_version(),
            "group": group,
            "submissions": submissions,
            "assignable_batches": assignable,
        },
    )


@router.post("/{raw_hash}/status", response_class=HTMLResponse, response_model=None)
async def candidates_update_status(
    request: Request,
    raw_hash: str,
    status: str = Form(...),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    if status not in WRITE_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    await update_candidate_status(ctx.engine, raw_hash=raw_hash, status=status)
    group = await get_candidate_group(ctx.engine, raw_hash=raw_hash)
    if group is None:
        raise HTTPException(status_code=404, detail="candidate group not found")
    return templates.TemplateResponse(
        "admin/candidates/_status.html",
        {"request": request, "group": group},
    )


@router.post("/{raw_hash}/notes", response_class=HTMLResponse, response_model=None)
async def candidates_update_notes(
    request: Request,
    raw_hash: str,
    notes: str = Form(""),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    await update_candidate_notes(ctx.engine, raw_hash=raw_hash, notes=notes)
    group = await get_candidate_group(ctx.engine, raw_hash=raw_hash)
    if group is None:
        raise HTTPException(status_code=404, detail="candidate group not found")
    return templates.TemplateResponse(
        "admin/candidates/_notes.html",
        {"request": request, "group": group},
    )


@router.post("/{raw_hash}/batches", response_class=HTMLResponse, response_model=None)
async def candidates_assign_batch(
    request: Request,
    raw_hash: str,
    batch_id: str = Form(...),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    await assign_candidates(
        ctx.engine,
        batch_id=batch_id,
        raw_address_hashes=[raw_hash],
        assigned_by=ctx.user.email,
    )
    return RedirectResponse(url=f"/admin/candidates/{raw_hash}", status_code=303)


@router.post(
    "/{raw_hash}/batches/{batch_slug}/unassign",
    response_class=HTMLResponse,
    response_model=None,
)
async def candidates_unassign_batch(
    request: Request,
    raw_hash: str,
    batch_slug: str,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    batch = await get_batch_by_slug(ctx.engine, slug=batch_slug)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    await unassign_candidates(ctx.engine, batch_id=batch["id"], raw_address_hashes=[raw_hash])
    return RedirectResponse(url=f"/admin/candidates/{raw_hash}", status_code=303)
