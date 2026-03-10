"""Low-level Google Address Validation API HTTP client.

Handles request construction (with ``enableUspsCass: true``), API key
authentication, and normalisation of the raw JSON response to a
provider-neutral dict consumed by
:class:`~services.validation.google_provider.GoogleProvider`.

Callers should not instantiate this class directly; use
:func:`~services.validation.factory.get_provider` instead.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"


class GoogleClient:
    """Async Google Address Validation API client.

    Parameters
    ----------
    api_key:
        Google Cloud API key restricted to the Address Validation API.
    http_client:
        Shared :class:`httpx.AsyncClient` instance (caller owns lifecycle).
    """

    def __init__(self, api_key: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http_client

    async def validate_address(
        self,
        street_address: str,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
    ) -> dict[str, Any]:
        """Validate a single US address via the Google Address Validation API.

        Returns a normalised dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``,
        ``latitude``, ``longitude``,
        ``has_inferred_components``, ``has_replaced_components``,
        ``has_unconfirmed_components``.

        Raises :class:`httpx.HTTPStatusError` on non-2xx responses.
        """
        address_lines = [street_address]
        city_state_zip = " ".join(p for p in (city, state, zip_code) if p)
        if city_state_zip:
            address_lines.append(city_state_zip)

        logger.debug("GoogleClient: validating address, %d lines", len(address_lines))
        resp = await self._http.post(
            _VALIDATE_URL,
            params={"key": self._api_key},
            json={
                "address": {"addressLines": address_lines},
                "enableUspsCass": True,
            },
        )
        resp.raise_for_status()
        raw: dict[str, Any] = resp.json()
        return self._map_response(raw)

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
            "latitude": lat if lat is not None else None,
            "longitude": lng if lng is not None else None,
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }
