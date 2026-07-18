"""Top-level admin router — mounts all dashboard sub-routers and exposes
the app-level exception-handler registration for admin errors."""

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import RedirectResponse

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.audit_views import router as audit_router
from address_validator.routers.admin.batches import router as batches_router
from address_validator.routers.admin.candidates import router as candidates_router
from address_validator.routers.admin.dashboard import router as dashboard_router
from address_validator.routers.admin.deps import AdminAuthRequired, DatabaseUnavailable
from address_validator.routers.admin.endpoints import router as endpoints_router
from address_validator.routers.admin.partials import router as partials_router
from address_validator.routers.admin.providers import router as providers_router

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard_router)
admin_router.include_router(audit_router)
admin_router.include_router(batches_router)
admin_router.include_router(candidates_router)
admin_router.include_router(endpoints_router)
admin_router.include_router(providers_router)
admin_router.include_router(partials_router)


async def _admin_auth_redirect(request: Request, exc: AdminAuthRequired) -> Response:
    return RedirectResponse(url=exc.redirect_url, status_code=302)


async def _admin_db_unavailable(request: Request, exc: DatabaseUnavailable) -> Response:
    return templates.TemplateResponse(
        "admin/error_503.html",
        {
            "request": request,
            "user": exc.user,
            "active_nav": "",
            "css_version": get_css_version(),
        },
        status_code=503,
    )


def register_admin_exception_handlers(app: FastAPI) -> None:
    """Register the admin app-level exception handlers on *app*.

    FastAPI exception handlers are app-scoped, not router-scoped, so the
    composition root must register them — this function keeps the handler
    bodies (and the admin template/config internals they touch) private to
    the admin package (GH #178).
    """
    app.add_exception_handler(AdminAuthRequired, _admin_auth_redirect)
    app.add_exception_handler(DatabaseUnavailable, _admin_db_unavailable)
