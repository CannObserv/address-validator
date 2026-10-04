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
from typing import Any, NamedTuple

import google.auth.exceptions
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest

from address_validator.core.validation_status import UNDETERMINED
from address_validator.services.validation._helpers import (
    _DPV_TO_STATUS,
    _warn_once,
    _warn_unknown_dpv,
)
from address_validator.services.validation._rate_limit import (
    _HTTP_BAD_REQUEST,
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    _TRANSIENT_DEFAULT_RETRY_AFTER_S,
    QuotaGuard,
    _json_object,
    _parse_retry_after,
    _raise_for_request_error,
    _raise_for_unexpected_status,
)
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderTransientError,
)

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"

# Verdict granularities that indicate the address was not geocodable at all.
_NON_GRANULAR: frozenset[str] = frozenset({"GRANULARITY_UNSPECIFIED", "OTHER", ""})


def _verdict_to_status(verdict: dict[str, Any]) -> str:
    """Derive a validation status from a Google verdict dict (no DPV code).

    Non-US answers use it as-is. The US non-CASS path never calls it for an
    ``addressComplete`` verdict (that is ``undetermined``, GH #262), so for US
    input it yields only ``invalid`` / ``not_found``.
    """
    if verdict.get("addressComplete"):
        return "confirmed"
    if verdict.get("validationGranularity", "") not in _NON_GRANULAR:
        return "invalid"
    return "not_found"


class _PostalFields(NamedTuple):
    """Subset of a Google postalAddress consumed by both US and non-US mappers."""

    address_line_1: str
    address_line_2: str
    city: str
    region: str
    postal_code: str


def _read_postal_address(postal_addr: dict[str, Any]) -> _PostalFields:
    """Extract address/city/region/postal fields from a Google postalAddress."""
    address_lines = postal_addr.get("addressLines", [])
    return _PostalFields(
        address_line_1=address_lines[0] if len(address_lines) > 0 else "",
        address_line_2=address_lines[1] if len(address_lines) > 1 else "",
        city=postal_addr.get("locality", ""),
        region=postal_addr.get("administrativeArea", ""),
        postal_code=postal_addr.get("postalCode", ""),
    )


def _split_folded_unit(line1: str, secondary: str | None) -> tuple[str, str]:
    """Split a secondary-unit suffix back out of a folded street line (GH #127).

    #126 folds the secondary-unit line into the Google request's single
    ``addressLines[0]`` (e.g. ``"9 BENNY DR LOT B"``).  On the non-CASS response
    path Google echoes the unit folded into one ``postalAddress.addressLines``
    element rather than as a separate line, so ``address_line_2`` would come back
    empty.  When *line1* ends with the unit we sent, strip it back into the
    secondary slot.  Match is case-insensitive; the echoed casing is preserved.

    Returns ``(street, unit)``; *unit* is ``""`` when there is no match (caller
    falls back to leaving the unit in *line1* — no regression vs. pre-#127).

    The match is a trusted bare-suffix match (no unit-designator boundary check):
    *secondary* is always the standardised unit line *we* sent, not arbitrary
    text, so a coincidental street-name collision is implausible.
    """
    sec = (secondary or "").strip()
    if not sec:
        return line1, ""
    if line1.lower().endswith(" " + sec.lower()):
        cut = len(line1) - len(sec)
        return line1[:cut].rstrip(), line1[cut:]
    return line1, ""


def _read_postal_address_with_unit(
    postal_addr: dict[str, Any], secondary: str | None
) -> _PostalFields:
    """Read a postalAddress, recovering a folded secondary unit (GH #127).

    Like :func:`_read_postal_address`, but when ``address_line_2`` comes back
    empty (Google echoed street + unit folded into one ``addressLines`` element)
    it splits the unit *we* sent back into the secondary slot via
    :func:`_split_folded_unit`.  Shared by the US non-CASS and non-US mappers.
    """
    fields = _read_postal_address(postal_addr)
    if fields.address_line_2:
        return fields
    line1, line2 = _split_folded_unit(fields.address_line_1, secondary)
    return fields._replace(address_line_1=line1, address_line_2=line2)


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

        Two ``RefreshError`` shapes are transient and map to
        ``ProviderTransientError``, like a USPS token-endpoint 5xx (GH-115):
        ``retryable=True`` (a token-endpoint 5xx google-auth's own retries could
        not clear), and one caused by google-auth's ``TransportError`` — Compute
        Engine credentials wrap a metadata-server network failure that way,
        with ``retryable`` left False.  Any other ``RefreshError`` (bad or
        revoked credentials) propagates: operator action, not a fallback.
        """
        if not self._credentials.valid:
            try:
                await asyncio.to_thread(self._credentials.refresh, AuthRequest())
            except google.auth.exceptions.RefreshError as exc:
                if not (
                    exc.retryable
                    or isinstance(exc.__cause__, google.auth.exceptions.TransportError)
                ):
                    raise
                # Fixed text: the exception carries the token endpoint's error body.
                logger.warning("GoogleClient: credential refresh failed transiently")
                raise ProviderTransientError(
                    "google", retry_after_seconds=_TRANSIENT_DEFAULT_RETRY_AFTER_S
                ) from exc
        return {"Authorization": f"Bearer {self._credentials.token}"}

    async def validate_address(
        self,
        street_address: str,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        country: str = "US",
        secondary_address: str | None = None,
    ) -> dict[str, Any]:
        """Validate a single address via the Google Address Validation API.

        *secondary_address* carries the secondary-unit line (e.g. ``"LOT B"``);
        when present it is folded into the street ``addressLines`` entry so the
        unit reaches the API and is not dropped (GH #126).

        Retries up to :data:`~services.validation._rate_limit._RETRY_MAX` times
        on HTTP 429, honouring the ``Retry-After`` header when present and
        falling back to exponential backoff.

        Returns a normalised dict with keys:
        ``status``, ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``,
        ``latitude``, ``longitude``,
        ``has_inferred_components``, ``has_replaced_components``,
        ``has_unconfirmed_components``, ``cass_standardized``.

        ``status`` is always present.  ``dpv_match_code`` is ``None`` for
        non-US addresses (USPS-specific field).  ``cass_standardized`` is
        ``True`` only when the address fields come from USPS CASS
        ``standardizedAddress`` (Pub 28); otherwise they are Google's
        ``postalAddress`` text (GH #263).

        Raises:
            ProviderBadRequestError: on HTTP 400 (input the provider rejects)
                or HTTP 401/403 (operator action required: rotate credentials
                or fix IAM).
            ProviderRateLimitedError: on HTTP 429 after all retries exhausted.
            ProviderTransientError: on HTTP 5xx, any other unexpected
                non-2xx response, a failed request (connect error, timeout,
                undecodable body) on the API call, a 2xx body that is not a
                JSON object (GH #271), or a transient credential-refresh
                failure (see ``_get_auth_headers``).
        """
        # Fold the secondary-unit line into the street line so Google receives
        # the full delivery point (e.g. "9 BENNY DR LOT B"). Omitting it drops
        # the unit from the validated result (GH #126).
        if secondary_address:
            street_line = f"{street_address} {secondary_address}".strip()
        else:
            street_line = street_address
        address_lines = [street_line]
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
            try:
                resp = await self._http.post(
                    _VALIDATE_URL,
                    headers=await self._get_auth_headers(),
                    json=payload,
                )
            except (httpx.RequestError, google.auth.exceptions.TransportError) as exc:
                # google-auth raises its own TransportError when a credential
                # refresh cannot reach its token endpoint (GH #257).
                _raise_for_request_error(exc, provider="google", logger=logger)
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
                _raise_for_unexpected_status(exc, provider="google", logger=logger)

            raw = _json_object(resp, provider="google", logger=logger)
            if country == "US":
                return self._map_response(raw, secondary_address=secondary_address)
            return self._map_response_international(raw, secondary_address=secondary_address)

        # unreachable — satisfies the type checker
        raise ProviderRateLimitedError("google", retry_after_seconds=0.0)

    @staticmethod
    def _map_response(raw: dict[str, Any], secondary_address: str | None = None) -> dict[str, Any]:
        """Normalise US Google response; falls back to postalAddress when CASS produces no DPV.

        *secondary_address* is the unit line folded into the request (#126); on the
        non-CASS path it is used to split a folded unit back into ``address_line_2``
        (GH #127) when Google echoes street + unit as one ``addressLines`` element.

        On the non-CASS path the ZIP+4 extension is dropped: Google echoes the
        input's extension (``-0000``, the ZIP's last four digits) and nothing
        verified it (GH #263).
        """
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        usps = result.get("uspsData", {})
        std_addr = usps.get("standardizedAddress", {})
        geocode = result.get("geocode", {})
        location = geocode.get("location", {})

        lat = location.get("latitude")
        lng = location.get("longitude")

        if usps.get("errorMessage"):
            # Documented as populated "when USPS processing is suspended because
            # of the detection of artificially created addresses". The text is
            # undocumented free form, so it is not logged.
            cass_processed = usps.get("cassProcessed")
            if not (cass_processed is None or isinstance(cass_processed, bool)):
                # Documented as a bool; anything else is shown by type only.
                cass_processed = f"<{type(cass_processed).__name__}>"
            _warn_once(
                logger,
                f"errorMessage|cassProcessed={cass_processed}",
                "GoogleClient: uspsData.errorMessage present (cassProcessed=%s)",
                cass_processed,
            )

        dpv = (usps.get("dpvConfirmation") or "").strip() or None
        # Captured before an unknown code is dropped: the fields still come from CASS.
        dpv_present = dpv is not None

        if dpv_present:
            # CASS-confirmed: USPS standardizedAddress is authoritative.
            zip_code = std_addr.get("zipCode", "")
            zip_ext = std_addr.get("zipCodeExtension", "") or ""
            postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code
            address_line_1 = std_addr.get("firstAddressLine", "")
            address_line_2 = std_addr.get("secondAddressLine", "")
            city = std_addr.get("city", "")
            region = std_addr.get("state", "")
            status = _DPV_TO_STATUS.get(dpv, UNDETERMINED)
            if dpv not in _DPV_TO_STATUS:
                # Unknown code: drop it — ValidationResult.dpv_match_code is a Literal.
                _warn_unknown_dpv(logger, "GoogleClient", "uspsData.dpvConfirmation", dpv)
                dpv = None
        else:
            # No CASS DPV — read Google's postalAddress + verdict instead.
            postal_addr = result.get("address", {}).get("postalAddress", {})
            fields = _read_postal_address_with_unit(postal_addr, secondary_address)
            address_line_1 = fields.address_line_1
            address_line_2 = fields.address_line_2
            city = fields.city
            region = fields.region
            # Unverified ZIP+4 — keep the ZIP5 only (GH #263).
            postal_code = fields.postal_code.partition("-")[0]
            # addressComplete only says the components are consistent, not that
            # USPS delivers there: with no DPV code Google's own logic says FIX,
            # so a complete verdict is no determination (GH #262). The negative
            # verdicts (invalid / not_found) are kept.
            status = (
                _verdict_to_status(verdict)
                if (postal_addr or verdict.get("validationGranularity"))
                and not verdict.get("addressComplete")
                else UNDETERMINED
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
            "cass_standardized": dpv_present,
        }

    @staticmethod
    def _map_response_international(
        raw: dict[str, Any], secondary_address: str | None = None
    ) -> dict[str, Any]:
        """Normalise a non-US Google response; reads postalAddress + verdict (no USPS CASS).

        Like the US non-CASS path, the unit line is folded into the request's
        single ``addressLines[0]`` (#126); split it back into ``address_line_2``
        when Google echoes it folded (GH #127).
        """
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        postal_addr = result.get("address", {}).get("postalAddress", {})
        location = result.get("geocode", {}).get("location", {})

        fields = _read_postal_address_with_unit(postal_addr, secondary_address)

        return {
            "dpv_match_code": None,
            "status": _verdict_to_status(verdict),
            "address_line_1": fields.address_line_1,
            "address_line_2": fields.address_line_2,
            "city": fields.city,
            "region": fields.region,
            "postal_code": fields.postal_code,
            "vacant": None,
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
            "cass_standardized": False,
        }
