# src/address_validator/services/libpostal_client.py
"""Async client for the pelias/libpostal-service REST API.

Translates libpostal tag labels to ISO 19160-4 element names and
decomposes the composite ``road`` token via the bilingual street splitter.

The client holds a persistent ``httpx.AsyncClient`` connection.  Call
``aclose()`` during application shutdown (wired via lifespan in main.py).
"""

from __future__ import annotations

import logging

import httpx

from address_validator.services.street_splitter import split_road

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LibpostalUnavailableError(Exception):
    """Raised when the libpostal sidecar cannot be reached."""


# ---------------------------------------------------------------------------
# libpostal label → ISO 19160-4 element name
# ---------------------------------------------------------------------------

_TAG_MAP: dict[str, str] = {
    "house_number": "premise_number",
    "house": "premise_name",
    # "road" is handled separately via street_splitter
    "unit": "sub_premise_number",
    "level": "sub_premise_number",  # floor/level → sub-premise
    "staircase": "sub_premise_number",
    "entrance": "sub_premise_number",
    "po_box": "general_delivery",
    "postcode": "postcode",
    "suburb": "dependent_locality",
    "city_district": "dependent_locality",
    "city": "locality",
    "state_district": "dependent_locality",
    "state": "administrative_area",
    # "country" is intentionally excluded — already known from request
}


def _map_tags(raw: list[dict[str, str]]) -> dict[str, str]:
    """Map a libpostal response list to an ISO 19160-4 component dict.

    The ``road`` label is passed through the street splitter.  All other
    labels are mapped via ``_TAG_MAP``; unknown labels are dropped.
    Values are uppercased to match our standardisation convention.
    """
    result: dict[str, str] = {}
    for item in raw:
        label = item.get("label", "")
        value = item.get("value", "").strip()
        if not value:
            continue
        if label == "road":
            result.update(split_road(value))
        elif label in _TAG_MAP:
            iso_key = _TAG_MAP[label]
            result[iso_key] = value.upper()
    return result


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LibpostalClient:
    """Async HTTP client wrapping the pelias/libpostal-service REST API."""

    def __init__(self, base_url: str = "http://localhost:4400") -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(5.0),
        )

    async def parse(self, address: str) -> dict[str, str]:
        """Parse *address* and return an ISO 19160-4 component dict.

        Raises ``LibpostalUnavailableError`` when the sidecar cannot be
        reached or returns a non-200 status.
        """
        try:
            response = await self._http.get("/parse", params={"address": address})
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("libpostal sidecar unavailable: %s", exc)
            raise LibpostalUnavailableError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("libpostal sidecar returned %s", exc.response.status_code)
            raise LibpostalUnavailableError(str(exc)) from exc
        except RuntimeError as exc:
            # httpx raises RuntimeError when the client is closed (e.g. during shutdown)
            logger.warning("libpostal client not usable: %s", exc)
            raise LibpostalUnavailableError(str(exc)) from exc

        return _map_tags(response.json())

    async def health_check(self) -> bool:
        """Return True if the sidecar is reachable (responds with HTTP 2xx).

        Uses a lightweight GET /parse probe.  A 2xx status is sufficient —
        the response body is not inspected, so an empty parse result does
        not cause a false negative.
        """
        try:
            response = await self._http.get("/parse", params={"address": "1 main st"})
            return response.is_success
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, RuntimeError):
            return False

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()
