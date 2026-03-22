"""Shared configuration for admin dashboard views."""

import subprocess
from functools import lru_cache
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

_PKG_DIR = Path(__file__).resolve().parent.parent.parent  # src/address_validator/

templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))


@lru_cache(maxsize=1)
def get_css_version() -> str:
    """Return short git SHA for CSS cache-busting. Cached after first call."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            text=True,
        ).strip()
    except Exception:
        return "dev"


def get_quota_info(request: Request) -> list[dict]:
    """Read current quota state from the provider registry."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return []
    return registry.get_quota_info()
