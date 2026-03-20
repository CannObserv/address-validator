"""GCP Application Default Credentials loading and project ID resolution.

Provides:
- :func:`get_credentials` — loads ADC with cloud-platform scope
- :func:`resolve_project_id` — env var → ADC → None fallback chain
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import google.auth

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def get_credentials() -> tuple[Credentials, str | None]:
    """Load Application Default Credentials with cloud-platform scope.

    Returns
    -------
    (credentials, project)
        The ADC credentials and the associated project ID (may be ``None``
        if not discoverable from the credential source).

    Raises
    ------
    google.auth.exceptions.DefaultCredentialsError
        If no valid credentials are found.
    """
    credentials, project = google.auth.default(scopes=_SCOPES)
    logger.debug("gcp_auth: loaded ADC credentials (project=%s)", project)
    return credentials, project


def resolve_project_id(adc_project: str | None) -> str | None:
    """Resolve the GCP project ID via env var → ADC fallback.

    Parameters
    ----------
    adc_project:
        Project ID returned by :func:`get_credentials`.  Used as fallback
        when ``GOOGLE_PROJECT_ID`` env var is unset or empty.

    Returns
    -------
    str | None
        The resolved project ID, or ``None`` if neither source provides one.
    """
    env_project = os.environ.get("GOOGLE_PROJECT_ID", "").strip()
    if env_project:
        logger.debug("gcp_auth: project ID from GOOGLE_PROJECT_ID env var: %s", env_project)
        return env_project

    if adc_project:
        logger.debug("gcp_auth: project ID from ADC: %s", adc_project)
        return adc_project

    logger.warning("gcp_auth: could not resolve GCP project ID from env or ADC")
    return None
