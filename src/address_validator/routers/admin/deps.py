"""Admin dashboard dependency injection.

Provides ``AdminContext`` via FastAPI ``Depends()`` — bundles authenticated
user, database engine, and request into a single typed dependency.

Custom exceptions (``AdminAuthRequired``, ``DatabaseUnavailable``) let
dependencies abort requests; exception handlers in ``main.py`` convert them
to the appropriate HTML responses (302 redirect / 503 error page).
"""

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

# ---------------------------------------------------------------------------
# Custom exceptions — caught by app-level exception handlers in main.py
# ---------------------------------------------------------------------------


class AdminAuthRequired(Exception):
    """User is not authenticated via exe.dev proxy headers."""

    def __init__(self, redirect_url: str) -> None:
        self.redirect_url = redirect_url
        super().__init__(redirect_url)


class DatabaseUnavailable(Exception):
    """Database engine is not configured or not initialised."""

    def __init__(self, user: "AdminUser") -> None:
        self.user = user
        super().__init__("database engine not configured")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminUser:
    """Authenticated admin user from exe.dev proxy headers."""

    user_id: str
    email: str


@dataclass(frozen=True)
class AdminContext:
    """Composite dependency injected into every admin route handler."""

    user: AdminUser
    engine: AsyncEngine
    request: Request


# ---------------------------------------------------------------------------
# Dependency functions (used with FastAPI Depends())
# ---------------------------------------------------------------------------


def get_admin_user(request: Request) -> AdminUser:
    """Read exe.dev proxy headers; raise ``AdminAuthRequired`` if absent."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")

    if not user_id or not email:
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        raise AdminAuthRequired(
            redirect_url=f"/__exe.dev/login?redirect={quote(next_url)}",
        )

    return AdminUser(user_id=user_id, email=email)


def get_admin_context(request: Request) -> AdminContext:
    """Composite dependency — auth first, then engine.

    Raises ``AdminAuthRequired`` if unauthenticated.
    Raises ``DatabaseUnavailable`` if engine is None (carries the user for
    the 503 template).
    """
    user = get_admin_user(request)
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise DatabaseUnavailable(user=user)
    return AdminContext(user=user, engine=engine, request=request)
