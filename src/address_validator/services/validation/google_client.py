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

from address_validator.services.validation._helpers import _DPV_TO_STATUS
from address_validator.services.validation._rate_limit import (
    _HTTP_BAD_REQUEST,
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    QuotaGuard,
    _parse_retry_after,
)
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"

# Verdict granularities that indicate the address was not geocodable at all.
_NON_GRANULAR: frozenset[str] = frozenset({"GRANULARITY_UNSPECIFIED", "OTHER", ""})


def _verdict_to_status(verdict: dict[str, Any]) -> str:
    """Derive a validation status from a non-US Google verdict dict."""
    if verdict.get("addressComplete"):
        return "confirmed"
    if verdict.get("validationGranularity", "") not in _NON_GRANULAR:
        return "invalid"
    return "not_found"


def _read_postal_address(postal_addr: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Extract (line_1, line_2, city, region, postal_code) from a Google postalAddress."""
    address_lines = postal_addr.get("addressLines", [])
    address_line_1 = address_lines[0] if len(address_lines) > 0 else ""
    address_line_2 = address_lines[1] if len(address_lines) > 1 else ""
    return (
        address_line_1,
        address_line_2,
        postal_addr.get("locality", ""),
        postal_addr.get("administrativeArea", ""),
        postal_addr.get("postalCode", ""),
    )


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
        country: str = "US",
    ) -> dict[str, Any]:
        """Validate a single address via the Google Address Validation API.

        Retries up to :data:`~services.validation._rate_limit._RETRY_MAX` times
        on HTTP 429, honouring the ``Retry-After`` header when present and
        falling back to exponential backoff.

        Returns a normalised dict with keys:
        ``status``, ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``,
        ``latitude``, ``longitude``,
        ``has_inferred_components``, ``has_replaced_components``,
        ``has_unconfirmed_components``.

        ``status`` is always present.  ``dpv_match_code`` is ``None`` for
        non-US addresses (USPS-specific field).

        Raises:
            ProviderBadRequestError: on HTTP 400 (input the provider rejects).
            ProviderRateLimitedError: on HTTP 429 after all retries exhausted.
            httpx.HTTPStatusError: on any other non-2xx response.
        """
        address_lines = [street_address]
        city_state_zip = " ".join(p for p in (city, state, zip_code) if p)
        if city_state_zip:
            address_lines.append(city_state_zip)

        if country == "US":
            payload: dict[str, Any] = {
                "address": {"addressLines": address_lines},
                "enableUspsCass": True,
            }
        else:
            payload = {
                "address": {
                    "addressLines": address_lines,
                    "regionCode": country,
                },
            }

        for attempt in range(_RETRY_MAX + 1):
            await self._rate_limiter.acquire()
            logger.debug(
                "GoogleClient: validating address, %d lines, country=%s",
                len(address_lines),
                country,
            )
            resp = await self._http.post(
                _VALIDATE_URL,
                headers=await self._get_auth_headers(),
                json=payload,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _HTTP_BAD_REQUEST:
                    logger.warning("GoogleClient: 400 Bad Request from Google API")
                    raise ProviderBadRequestError("google", detail="HTTP 400") from exc
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
            if country == "US":
                return self._map_response(raw)
            return self._map_response_international(raw)

        # unreachable — satisfies the type checker
        raise ProviderRateLimitedError("google", retry_after_seconds=0.0)

    @staticmethod
    def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise US Google response; falls back to postalAddress when CASS produces no DPV."""
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        usps = result.get("uspsData", {})
        std_addr = usps.get("standardizedAddress", {})
        geocode = result.get("geocode", {})
        location = geocode.get("location", {})

        lat = location.get("latitude")
        lng = location.get("longitude")

        dpv = usps.get("dpvConfirmation") or None

        if dpv is not None:
            # CASS-confirmed: USPS standardizedAddress is authoritative.
            zip_code = std_addr.get("zipCode", "")
            zip_ext = std_addr.get("zipCodeExtension", "") or ""
            postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code
            address_line_1 = std_addr.get("firstAddressLine", "")
            address_line_2 = std_addr.get("secondAddressLine", "")
            city = std_addr.get("city", "")
            region = std_addr.get("state", "")
            status = _DPV_TO_STATUS.get(dpv, "unavailable")
        else:
            # No CASS DPV — read Google's postalAddress + verdict instead.
            postal_addr = result.get("address", {}).get("postalAddress", {})
            address_line_1, address_line_2, city, region, postal_code = _read_postal_address(
                postal_addr
            )
            status = (
                _verdict_to_status(verdict)
                if postal_addr or verdict.get("validationGranularity")
                else "unavailable"
            )

        return {
            "dpv_match_code": dpv,
            "status": status,
            "address_line_1": address_line_1,
            "address_line_2": address_line_2,
            "city": city,
            "region": region,
            "postal_code": postal_code,
            "vacant": usps.get("dpvVacant") or None,
            "latitude": lat,
            "longitude": lng,
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }

    @staticmethod
    def _map_response_international(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise a non-US Google response; reads postalAddress + verdict (no USPS CASS)."""
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        postal_addr = result.get("address", {}).get("postalAddress", {})
        location = result.get("geocode", {}).get("location", {})

        address_line_1, address_line_2, city, region, postal_code = _read_postal_address(
            postal_addr
        )

        return {
            "dpv_match_code": None,
            "status": _verdict_to_status(verdict),
            "address_line_1": address_line_1,
            "address_line_2": address_line_2,
            "city": city,
            "region": region,
            "postal_code": postal_code,
            "vacant": None,
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }
