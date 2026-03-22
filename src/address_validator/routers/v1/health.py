"""Health check endpoint — unauthenticated, no business logic."""

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from address_validator.models import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description="Returns service and database health. No auth required.",
    responses={503: {"description": "Service degraded — database unreachable"}},
)
async def health(request: Request, response: Response) -> HealthResponse:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return HealthResponse(database="unconfigured")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return HealthResponse(database="ok")
    except Exception:
        response.status_code = 503
        return HealthResponse(status="degraded", database="error")
