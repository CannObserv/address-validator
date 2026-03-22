"""Low-level Google Address Validation API HTTP client.

Handles request construction (with ``enableUspsCass: true``), ADC bearer token
authentication, quota enforcement via a :class:`~services.validation._rate_limit.QuotaGuard`,
exponential-backoff retry on HTTP 429, and normalisation of the raw JSON response to a
provider-neutral dict consumed by
:class:`~services.validation.google_provider.GoogleProvider`.

Callers should not instantiate this class directly; use
:class:`~services.validation.registry.ProviderRegistry` instead.
"""

import asyncio
import logging
from typing import Any

import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest

from address_validator.services.validation._rate_limit import (
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    QuotaGuard,
    _parse_retry_after,
)
from address_validator.services.validation.errors import ProviderRateLimitedError

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"


class GoogleClient:
    """Async Google Address Validation API client.

    Parameters
    ----------
    credentials:
        Google ADC credentials object used for bearer token authentication.
    http_client:
        Shared :class:`httpx.AsyncClient` instance (caller owns lifecycle).
    quota_guard:
        :class:`~services.validation._rate_limit.QuotaGuard` instance
        managing rate limits and quota constraints.
    """

    def __init__(
        self,
        credentials: Credentials,
        http_client: httpx.AsyncClient,
        quota_guard: QuotaGuard,
    ) -> None:
        self._credentials = credentials
        self._http = http_client
        self._rate_limiter = quota_guard

    @property
    def quota_guard(self) -> QuotaGuard:
        """Expose the rate limiter for quota state inspection."""
        return self._rate_limiter

    async def _get_auth_headers(self) -> dict[str, str]:
        """Return Authorization header with a fresh bearer token.

        Credential refresh is a blocking HTTP call (token endpoint or metadata
        server).  We offload it to a thread to avoid stalling the event loop.
        Refreshes are infrequent (~once per hour).
        """
        if not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, AuthRequest())
        return {"Authorization": f"Bearer {self._credentials.token}"}

    async def validate_address(
        self,
        street_address: str,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
    ) -> dict[str, Any]:
        """Validate a single US address via the Google Address Validation API.

        Retries up to :data:`~services.validation._rate_limit._RETRY_MAX` times
        on HTTP 429, honouring the ``Retry-After`` header when present and
        falling back to exponential backoff.  Raises
        :class:`~services.validation.errors.ProviderRateLimitedError` when all
        retries are exhausted.

        Returns a normalised dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``,
        ``latitude``, ``longitude``,
        ``has_inferred_components``, ``has_replaced_components``,
        ``has_unconfirmed_components``.

        Raises :class:`httpx.HTTPStatusError` on non-429 non-2xx responses.
        """
        address_lines = [street_address]
        city_state_zip = " ".join(p for p in (city, state, zip_code) if p)
        if city_state_zip:
            address_lines.append(city_state_zip)

        for attempt in range(_RETRY_MAX + 1):
            await self._rate_limiter.acquire()
            logger.debug("GoogleClient: validating address, %d lines", len(address_lines))
            resp = await self._http.post(
                _VALIDATE_URL,
                headers=await self._get_auth_headers(),
                json={
                    "address": {"addressLines": address_lines},
                    "enableUspsCass": True,
                },
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _HTTP_TOO_MANY_REQUESTS:
                    if attempt < _RETRY_MAX:
                        delay = _parse_retry_after(exc.response, attempt)
                        logger.warning(
                            "GoogleClient: 429 received, retrying in %.1fs (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            _RETRY_MAX,
                        )
                        await asyncio.sleep(delay)
                        continue
                    delay = _parse_retry_after(exc.response, attempt)
                    raise ProviderRateLimitedError("google", retry_after_seconds=delay) from exc
                raise

            raw: dict[str, Any] = resp.json()
            return self._map_response(raw)

        # unreachable — satisfies the type checker
        raise ProviderRateLimitedError("google", retry_after_seconds=0.0)

    @staticmethod
    def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise the Google Address Validation API JSON response."""
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        usps = result.get("uspsData", {})
        std_addr = usps.get("standardizedAddress", {})
        geocode = result.get("geocode", {})
        location = geocode.get("location", {})

        zip_code = std_addr.get("zipCode", "")
        zip_ext = std_addr.get("zipCodeExtension", "") or ""
        postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code

        lat = location.get("latitude")
        lng = location.get("longitude")

        return {
            "dpv_match_code": usps.get("dpvConfirmation") or None,
            "address_line_1": std_addr.get("firstAddressLine", ""),
            "address_line_2": std_addr.get("secondAddressLine", ""),
            "city": std_addr.get("city", ""),
            "region": std_addr.get("state", ""),
            "postal_code": postal_code,
            "vacant": usps.get("dpvVacant") or None,
            "latitude": lat,
            "longitude": lng,
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }
