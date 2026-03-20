"""Shared configuration for admin dashboard views."""

import subprocess
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from address_validator.services.validation import factory

_PKG_DIR = Path(__file__).resolve().parent.parent.parent  # src/address_validator/

templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

STATIC_DIR = str(_PKG_DIR / "static" / "admin")


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


def get_quota_info() -> list[dict]:
    """Read current quota state from provider singletons."""
    quota = []
    usps = factory._usps_provider  # noqa: SLF001
    if usps and hasattr(usps, "_client") and hasattr(usps._client, "_rate_limiter"):  # noqa: SLF001
        guard = usps._client._rate_limiter  # noqa: SLF001
        if len(guard._windows) > 1:  # noqa: SLF001
            quota.append(
                {
                    "provider": "usps",
                    "remaining": int(guard._tokens[1]),  # noqa: SLF001
                    "limit": guard._windows[1].limit,  # noqa: SLF001
                }
            )
    google = factory._google_provider  # noqa: SLF001
    if google and hasattr(google, "_client") and hasattr(google._client, "_rate_limiter"):  # noqa: SLF001
        guard = google._client._rate_limiter  # noqa: SLF001
        if len(guard._windows) > 1:  # noqa: SLF001
            quota.append(
                {
                    "provider": "google",
                    "remaining": int(guard._tokens[1]),  # noqa: SLF001
                    "limit": guard._windows[1].limit,  # noqa: SLF001
                }
            )
    return quota
