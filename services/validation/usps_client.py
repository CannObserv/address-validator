"""Low-level USPS Addresses API v3 HTTP client.

Handles OAuth2 client-credentials token acquisition and caching, a simple
token-bucket rate limiter (5 req/s matching the free-tier limit), and
mapping of the raw USPS JSON response to a normalised dict consumed by
:class:`~services.validation.usps_provider.USPSProvider`.

Callers should not instantiate this class directly; use
:func:`~services.validation.factory.get_provider` instead.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"  # noqa: S105
_ADDRESS_URL = "https://apis.usps.com/addresses/v3/address"

# Token is refreshed 60 s before it actually expires to avoid races.
_TOKEN_REFRESH_BUFFER_S = 60

# Free-tier rate limit: 5 requests/second.
_RATE_LIMIT_RPS = 5.0


@dataclass
class USPSToken:
    access_token: str
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) >= self.expires_at


@dataclass
class _TokenBucket:
    """Minimal token-bucket rate limiter."""

    rate: float  # tokens per second
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = monotonic()

    async def acquire(self) -> None:
        async with self._lock:
            now = monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


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
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._http = http_client
        self._token: USPSToken | None = None
        self._rate_limiter = _TokenBucket(rate=_RATE_LIMIT_RPS, capacity=_RATE_LIMIT_RPS)

    async def _get_token(self) -> str:
        """Return a valid access token, fetching a new one if needed."""
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

        Returns a normalised dict with keys:
        ``dpv_match_code``, ``zip_plus4``, ``vacant``, ``corrected_components``.

        Raises :class:`httpx.HTTPStatusError` on non-2xx responses.
        """
        await self._rate_limiter.acquire()
        token = await self._get_token()

        params: dict[str, str] = {"streetAddress": street_address}
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if zip_code:
            params["ZIPCode"] = zip_code

        resp = await self._http.get(
            _ADDRESS_URL,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        resp.raise_for_status()
        raw: dict[str, Any] = resp.json()

        return self._map_response(raw)

    @staticmethod
    def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise the USPS v3 JSON response to a provider-neutral dict."""
        addr = raw.get("address", {})
        extra = raw.get("addressAdditionalInfo", {})

        postal_code = addr.get("ZIPCode", "")
        zip_plus4_raw = addr.get("ZIPPlus4", None)

        # Build corrected components only when we have at least a street.
        corrected: dict[str, str] | None = None
        if addr.get("streetAddress"):
            corrected = {
                "address_line": addr.get("streetAddress", ""),
                "secondary_address": addr.get("secondaryAddress", ""),
                "city": addr.get("city", ""),
                "region": addr.get("state", ""),
                "postal_code": postal_code,
            }

        return {
            "dpv_match_code": extra.get("DPVConfirmation") or None,
            "zip_plus4": zip_plus4_raw or None,
            "vacant": extra.get("vacant") or None,
            "corrected_components": corrected,
        }
