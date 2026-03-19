"""Health check endpoint — unauthenticated, no business logic."""

from fastapi import APIRouter

from address_validator.models import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description='Returns `{"status": "ok"}` when the service is running. No auth required.',
)
def health() -> HealthResponse:
    return HealthResponse()
