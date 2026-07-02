"""Audit log view — paginated, filterable audit trail."""

import math
from typing import Annotated

from annotated_types import Ge, Le
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BeforeValidator
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_audit_rows

router = APIRouter(prefix="/audit")

_PER_PAGE = 50

# Recent-window options (days) for the raw_input substring filter. Bounding the
# scan keeps the admin view responsive as audit_log grows (#152).
_RAW_INPUT_WINDOWS = (7, 30, 90)
_DEFAULT_RAW_INPUT_DAYS = 7


def _blank_to_none(value: object) -> object:
    """Coerce an empty-string query param to None.

    The HTMX filter form submits every named input on each request, so a blank
    "Min Status" field arrives as ``status_min=`` (empty string). Without this,
    Pydantic rejects empty string for ``int | None`` and the whole request 422s.
    """
    return None if value == "" else value


# Optional HTTP-status filter that tolerates the empty string sent by blank form
# fields. The Ge/Le bounds live on the int branch so they are skipped when the
# coerced value is None (a union-level constraint would raise on None instead).
OptionalStatusMin = Annotated[
    Annotated[int, Ge(100), Le(599)] | None, BeforeValidator(_blank_to_none)
]

# raw_input window (days). Tolerates a blank submit (coerced to None → default by
# the membership check below); out-of-range ints likewise fall back to the default.
OptionalRawInputDays = Annotated[int | None, BeforeValidator(_blank_to_none)]


@router.get("/", response_class=HTMLResponse, response_model=None)
async def audit_list(
    request: Request,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    endpoint: str | None = Query(None),
    status_min: OptionalStatusMin = None,
    raw_input: str | None = Query(None),
    raw_input_days: OptionalRawInputDays = _DEFAULT_RAW_INPUT_DAYS,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    # Constrain to the offered windows; a substring scan over the hot audit_log
    # table must stay bounded (#152). Blank/unknown values fall back to the default.
    if raw_input_days not in _RAW_INPUT_WINDOWS:
        raw_input_days = _DEFAULT_RAW_INPUT_DAYS

    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        endpoint=endpoint,
        client_ip=client_ip,
        status_min=status_min,
        raw_input=raw_input,
        raw_input_days=raw_input_days,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {
        "client_ip": client_ip,
        "endpoint": endpoint,
        "status_min": status_min,
        "raw_input": raw_input,
        "raw_input_days": raw_input_days,
    }

    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows, "show_result": False, "show_provider": True},
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
            "raw_input_windows": _RAW_INPUT_WINDOWS,
            "show_result": False,
            "show_provider": True,
        },
    )
