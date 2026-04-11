"""Health check endpoint — unauthenticated, no business logic."""

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from address_validator.models import HealthResponseV2

router = APIRouter(prefix="/api/v2", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponseV2,
    summary="Service health check",
    description="Returns service, database, and libpostal sidecar health. No auth required.",
    responses={503: {"description": "Service degraded — database unreachable"}},
)
async def health(request: Request, response: Response) -> HealthResponseV2:
    libpostal_client = getattr(request.app.state, "libpostal_client", None)
    if libpostal_client is not None and await libpostal_client.health_check():
        libpostal_status = "ok"
    else:
        libpostal_status = "unavailable"

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return HealthResponseV2(database="unconfigured", libpostal=libpostal_status)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return HealthResponseV2(database="ok", libpostal=libpostal_status)
    except Exception:
        response.status_code = 503
        return HealthResponseV2(status="degraded", database="error", libpostal=libpostal_status)
