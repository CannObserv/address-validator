"""Top-level admin router — mounts all dashboard sub-routers."""

from fastapi import APIRouter

from address_validator.routers.admin.audit_views import router as audit_router
from address_validator.routers.admin.candidates import router as candidates_router
from address_validator.routers.admin.dashboard import router as dashboard_router
from address_validator.routers.admin.endpoints import router as endpoints_router
from address_validator.routers.admin.partials import router as partials_router
from address_validator.routers.admin.providers import router as providers_router

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard_router)
admin_router.include_router(audit_router)
admin_router.include_router(candidates_router)
admin_router.include_router(endpoints_router)
admin_router.include_router(providers_router)
admin_router.include_router(partials_router)
