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


def _is_libpostal_payload(raw: object) -> bool:
    """Return True if *raw* meets ``_map_tags``' preconditions: a list of
    objects whose ``label``/``value``, when present, are strings."""
    return isinstance(raw, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("label", ""), str)
        and isinstance(item.get("value", ""), str)
        for item in raw
    )


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

    def __init__(
        self,
        base_url: str = "http://localhost:4400",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """*transport* overrides httpx's network transport (tests pass
        ``httpx.MockTransport``); ``None`` uses the default."""
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(5.0),
            transport=transport,
        )

    async def parse(self, address: str) -> dict[str, str]:
        """Parse *address* and return an ISO 19160-4 component dict.

        Raises ``LibpostalUnavailableError`` when the sidecar cannot be
        reached, drops the connection, returns a non-200 status, or returns
        a body that is not a JSON list of string-valued ``label``/``value``
        objects.
        """
        try:
            response = await self._http.get("/parse", params={"address": address})
            response.raise_for_status()
        except httpx.RequestError as exc:
            # RequestError, not NetworkError/TimeoutException: Docker's port proxy
            # accepts then drops the connection while the container warms up,
            # raising RemoteProtocolError (a ProtocolError, not a NetworkError).
            # See GH #239.
            logger.warning("libpostal sidecar unavailable: %s", exc)
            raise LibpostalUnavailableError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("libpostal sidecar returned %s", exc.response.status_code)
            # Status code only, never str(exc): httpx embeds the full request URL
            # in HTTPStatusError's message, and this request is
            # GET /parse?address=<the user's address>. Nothing logs this message
            # today, but it travels up through three call sites and a
            # `raise ... from`, so keeping the address out of it is the cheap
            # guard. See GH #185 and PINNED_LOGGER_LEVELS in core/logging.py.
            raise LibpostalUnavailableError(
                f"libpostal sidecar returned {exc.response.status_code}"
            ) from exc
        except RuntimeError as exc:
            # httpx raises RuntimeError when the client is closed (e.g. during shutdown)
            logger.warning("libpostal client not usable: %s", exc)
            raise LibpostalUnavailableError(str(exc)) from exc

        try:
            raw = response.json()
        except ValueError as exc:
            # Non-JSON 2xx body (json.JSONDecodeError and UnicodeDecodeError are
            # both ValueError).  Fixed message, never the decoder's: the body
            # is a parse of the user's address.
            logger.warning("libpostal sidecar returned a non-JSON body")
            raise LibpostalUnavailableError("libpostal sidecar returned a non-JSON body") from exc

        if not _is_libpostal_payload(raw):
            # _map_tags would raise AttributeError/TypeError → unhandled 500.
            logger.warning("libpostal sidecar returned an unexpected JSON shape")
            raise LibpostalUnavailableError("libpostal sidecar returned an unexpected JSON shape")

        return _map_tags(raw)

    async def probe(self) -> str | None:
        """Return ``None`` if the sidecar is reachable (HTTP 2xx), else a short reason.

        The reason is the exception class name (``RemoteProtocolError``,
        ``ConnectError``, ``ReadTimeout``, ``RuntimeError``) or ``HTTP <status>``
        for a non-2xx.  Class name only, never ``str(exc)`` — enough to diagnose
        and keeps the message fixed.  Logs nothing: the health route polls this
        via ``health_check()``, so only the boot-time caller logs the reason
        (GH #244).

        Uses a lightweight GET /parse probe.  A 2xx status is sufficient —
        the response body is not inspected, so an empty parse result does
        not cause a false negative.

        ``httpx.HTTPStatusError`` is intentionally absent: we do not call
        ``raise_for_status()``, so non-2xx responses are handled via
        ``response.is_success`` rather than raising.
        """
        try:
            response = await self._http.get("/parse", params={"address": "1 main st"})
        except (httpx.RequestError, RuntimeError) as exc:
            return type(exc).__name__
        return None if response.is_success else f"HTTP {response.status_code}"

    async def health_check(self) -> bool:
        """Return True if the sidecar is reachable (responds with HTTP 2xx)."""
        return await self.probe() is None

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()
