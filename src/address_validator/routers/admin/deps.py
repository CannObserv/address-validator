"""Admin dashboard authentication via exe.dev proxy headers.

The exe.dev reverse proxy injects X-ExeDev-UserID and X-ExeDev-Email headers
when the user is authenticated. If absent, the user needs to log in via
the /__exe.dev/login endpoint.

Any authenticated exe.dev user is treated as an admin (no RBAC).
"""

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Request
from starlette.responses import RedirectResponse


@dataclass(frozen=True)
class AdminUser:
    """Authenticated admin user from exe.dev proxy headers."""

    user_id: str
    email: str


def get_admin_user(request: Request) -> AdminUser | RedirectResponse:
    """Read exe.dev proxy headers and return AdminUser or redirect to login."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")

    if not user_id or not email:
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(
            url=f"/__exe.dev/login?redirect={quote(next_url)}",
            status_code=302,
        )

    return AdminUser(user_id=user_id, email=email)
