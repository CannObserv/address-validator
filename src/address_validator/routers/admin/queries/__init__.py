"""Admin dashboard query helpers — backward-compatible re-exports."""

from address_validator.routers.admin.queries.audit import get_audit_rows
from address_validator.routers.admin.queries.dashboard import (
    get_dashboard_stats,
    get_sparkline_data,
)
from address_validator.routers.admin.queries.endpoint import get_endpoint_stats
from address_validator.routers.admin.queries.provider import (
    get_provider_daily_usage,
    get_provider_stats,
)

__all__ = [
    "get_audit_rows",
    "get_dashboard_stats",
    "get_endpoint_stats",
    "get_provider_daily_usage",
    "get_provider_stats",
    "get_sparkline_data",
]
