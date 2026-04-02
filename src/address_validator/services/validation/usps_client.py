"""Low-level USPS Addresses API v3 HTTP client.

Handles OAuth2 client-credentials token acquisition and caching,
quota enforcement via a :class:`~services.validation._rate_limit.QuotaGuard`,
exponential-backoff retry on HTTP 429, and mapping of the raw USPS JSON
response to a normalised dict consumed by
:class:`~services.validation.usps_provider.USPSProvider`.

Callers should not instantiate this class directly; use
:class:`~services.validation.registry.ProviderRegistry` instead.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from address_validator.services.validation._rate_limit import (
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    QuotaGuard,
    _parse_retry_after,
)
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)

_HTTP_BAD_REQUEST = 400
_ZIP5_LENGTH = 5

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"  # noqa: S105
_ADDRESS_URL = "https://apis.usps.com/addresses/v3/address"

# Token is refreshed 60 s before it actually expires to avoid races.
_TOKEN_REFRESH_BUFFER_S = 60


@dataclass
class USPSToken:
    """Cached OAuth2 access token with expiry tracking."""

    access_token: str
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) >= self.expires_at


class USPSClient:
    """Async USPS Addresses API v3 client.

    Parameters
    ----------
    consumer_key:
        OAuth2 client ID from the USPS Developer Portal.
    consumer_secret:
        OAuth2 client secret.
    http_client:
        Shared :class:`httpx.AsyncClient` instance (caller owns lifecycle).
    quota_guard:
        A :class:`~services.validation._rate_limit.QuotaGuard` instance
        for rate limiting.
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        http_client: httpx.AsyncClient,
        quota_guard: QuotaGuard,
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._http = http_client
        self._token: USPSToken | None = None
        self._token_lock = asyncio.Lock()
        self._rate_limiter = quota_guard

    @property
    def quota_guard(self) -> QuotaGuard:
        """Expose the rate limiter for quota state inspection."""
        return self._rate_limiter

    async def _get_token(self) -> str:
        """Return a valid access token, fetching a new one if needed.

        The :attr:`_token_lock` ensures that concurrent requests on an
        expired token issue exactly one refresh rather than racing to
        fetch multiple tokens simultaneously.
        """
        async with self._token_lock:
            if self._token and not self._token.is_expired():
                return self._token.access_token

            logger.debug("USPSClient: fetching new OAuth2 token")
            resp = await self._http.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._consumer_key,
                    "client_secret": self._consumer_secret,
                },
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            expires_in: int = int(data.get("expires_in", 3600))
            self._token = USPSToken(
                access_token=data["access_token"],
                expires_at=datetime.now(tz=UTC)
                + timedelta(seconds=expires_in - _TOKEN_REFRESH_BUFFER_S),
            )
            return self._token.access_token

    async def validate_address(
        self,
        street_address: str,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
    ) -> dict[str, Any]:
        """Validate a single US address via the USPS Addresses API v3.

        Retries up to :data:`~services.validation._rate_limit._RETRY_MAX` times
        on HTTP 429, honouring the ``Retry-After`` header when present and
        falling back to exponential backoff.  Raises
        :class:`~services.validation.errors.ProviderRateLimitedError` when all
        retries are exhausted.

        Returns a normalised dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``.

        Raises :class:`~services.validation.errors.ProviderBadRequestError`
        when the USPS API returns HTTP 400 (malformed input).

        Raises :class:`httpx.HTTPStatusError` on other non-429 non-2xx responses.
        """
        params: dict[str, str] = {"streetAddress": street_address}
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if zip_code:
            # USPS v3 API rejects ZIP+4 in the ZIPCode param — strip to 5 digits.
            params["ZIPCode"] = (
                zip_code[:_ZIP5_LENGTH] if len(zip_code) > _ZIP5_LENGTH else zip_code
            )

        for attempt in range(_RETRY_MAX + 1):
            await self._rate_limiter.acquire()
            token = await self._get_token()
            resp = await self._http.get(
                _ADDRESS_URL,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _HTTP_BAD_REQUEST:
                    logger.warning(
                        "USPSClient: 400 Bad Request from USPS API: %s",
                        exc.response.text[:200] if hasattr(exc.response, "text") else str(exc),
                    )
                    raise ProviderBadRequestError("usps", detail=str(exc)) from exc
                if exc.response.status_code == _HTTP_TOO_MANY_REQUESTS:
                    if attempt < _RETRY_MAX:
                        delay = _parse_retry_after(exc.response, attempt)
                        logger.warning(
                            "USPSClient: 429 received, retrying in %.1fs (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            _RETRY_MAX,
                        )
                        await asyncio.sleep(delay)
                        continue
                    delay = _parse_retry_after(exc.response, attempt)
                    raise ProviderRateLimitedError("usps", retry_after_seconds=delay) from exc
                raise

            raw: dict[str, Any] = resp.json()
            return self._map_response(raw)

        # unreachable — satisfies the type checker
        raise ProviderRateLimitedError("usps", retry_after_seconds=0.0)

    @staticmethod
    def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise the USPS v3 JSON response to a provider-neutral dict.

        Returns a flat dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``.
        """
        addr = raw.get("address", {})
        extra = raw.get("additionalInfo", {})

        zip_code = addr.get("ZIPCode", "")
        zip_ext = addr.get("ZIPPlus4", "") or ""
        postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code

        return {
            "dpv_match_code": extra.get("DPVConfirmation") or None,
            "address_line_1": addr.get("streetAddress", ""),
            "address_line_2": addr.get("secondaryAddress", ""),
            "city": addr.get("city", ""),
            "region": addr.get("state", ""),
            "postal_code": postal_code,
            "vacant": extra.get("vacant") or None,
        }
