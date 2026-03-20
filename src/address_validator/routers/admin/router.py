"""Top-level admin router — mounts all dashboard sub-routers."""

from fastapi import APIRouter

from address_validator.routers.admin.dashboard import router as dashboard_router

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard_router)
