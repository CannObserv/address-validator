"""Admin batch views — list, detail, plan-new, transition status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

if TYPE_CHECKING:
    from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import (
    get_batch_by_slug,
    get_batch_candidates,
    list_batches,
)
from address_validator.services.training_batches import (
    InvalidTransitionError,
    create_batch,
    transition_status,
)

router = APIRouter(prefix="/batches")

_VALID_STATUSES: frozenset[str] = frozenset(
    {"planned", "active", "deployed", "observing", "closed"}
)


@router.get("/", response_class=HTMLResponse, response_model=None)
async def batches_list(
    request: Request,
    status: str | None = Query(None),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    filter_status = status if status in _VALID_STATUSES else None
    rows = await list_batches(ctx.engine, status=filter_status)
    return templates.TemplateResponse(
        "admin/batches/index.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "batches",
            "css_version": get_css_version(),
            "rows": rows,
            "filter_status": filter_status,
        },
    )


@router.post("/", response_class=HTMLResponse, response_model=None)
async def batches_create(
    request: Request,
    slug: str = Form(...),
    description: str = Form(...),
    targeted_failure_pattern: str = Form(""),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    pattern = targeted_failure_pattern.strip() or None
    await create_batch(
        ctx.engine,
        slug=slug.strip(),
        description=description.strip(),
        targeted_failure_pattern=pattern,
    )
    return RedirectResponse(url=f"/admin/batches/{slug.strip()}", status_code=303)


@router.get("/{slug}", response_class=HTMLResponse, response_model=None)
async def batches_detail(
    request: Request,
    slug: str,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    batch = await get_batch_by_slug(ctx.engine, slug=slug)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    candidates = await get_batch_candidates(ctx.engine, batch_id=batch["id"])
    return templates.TemplateResponse(
        "admin/batches/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "batches",
            "css_version": get_css_version(),
            "batch": batch,
            "candidates": candidates,
        },
    )


@router.post("/{slug}/status", response_class=HTMLResponse, response_model=None)
async def batches_transition(
    request: Request,
    slug: str,
    status: str = Form(...),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    batch = await get_batch_by_slug(ctx.engine, slug=slug)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    try:
        await transition_status(ctx.engine, batch_id=batch["id"], target=status)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/batches/{slug}", status_code=303)
