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
    _HTTP_BAD_REQUEST,
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    QuotaGuard,
    _parse_retry_after,
    _raise_for_unexpected_status,
)
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)

_ZIP5_LENGTH = 5

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://apis.usps.com"
_TOKEN_PATH = "/oauth2/v3/token"  # noqa: S105
_ADDRESS_PATH = "/addresses/v3/address"

# Token is refreshed 60 s before it actually expires to avoid races.
_TOKEN_REFRESH_BUFFER_S = 60

# Top-level keys in the USPS v3 response that we currently consume in
# _map_response. Anything else is fed to the recon logger (issue #122).
_CONSUMED_TOP_LEVEL_KEYS = frozenset({"address", "additionalInfo"})


def _normalise_flag(value: str | None) -> str | None:
    """Strip whitespace and coerce empty/whitespace-only to None.

    USPS uses " " (single space) as a "no determination" sentinel for some
    additionalInfo flags (DPVConfirmation, possibly others). This collapses
    None, "", and any whitespace-only value to None uniformly. Note:
    downstream ``ValidationResult.dpv_match_code`` is typed as
    ``Literal["Y","S","D","N"] | None`` — whitespace values would otherwise
    fail Pydantic validation at the response boundary.
    """
    return (value or "").strip() or None


def _bucket_list_length(n: int) -> str:
    """Bucket a non-empty list length so the recon signature collapses on variation.

    Assumes ``n >= 1`` — the caller in :func:`_summarise_shape` short-circuits
    the empty case (``"list[empty]"``) before delegating here. Once we know
    a list can hold "many" entries the exact count adds no structural
    information; bucketing prevents :data:`USPSClient._recon_seen_signatures`
    from growing one entry per distinct length over the process lifetime.
    """
    if n == 1:
        return "1"
    return "many"


def _summarise_shape(value: Any) -> str:
    """Return a structural label for a value without revealing its contents.

    PII safety: never includes scalar values. For dicts/lists, returns only
    schema-level info (key names, length buckets, element shape). USPS keys
    (``firm``, ``corrections``, ``matches``) can carry address content;
    only their *structure* is safe to log at INFO.
    """
    if isinstance(value, list):
        if not value:
            return "list[empty]"
        return f"list[len={_bucket_list_length(len(value))},item={_summarise_shape(value[0])}]"
    if isinstance(value, dict):
        return f"dict[keys={sorted(value.keys())}]"
    # Scalar / None — name only, never the value. bool falls through to
    # ``type(value).__name__`` (which returns "bool").
    if value is None:
        return "none"
    return type(value).__name__


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
    api_base:
        Scheme+host prefix for the USPS API (trailing slash tolerated).
        Defaults to production; override via ``USPS_API_BASE`` for TEM or an
        endpoint host switch (GH #155).
    """

    # Class-level dedup set for recon logging (issue #122). Spans the
    # process lifetime so each unique structural signature is logged at
    # most once; reset via :meth:`_reset_recon_state` in tests.
    _recon_seen_signatures: set[str] = set()  # noqa: RUF012

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        http_client: httpx.AsyncClient,
        quota_guard: QuotaGuard,
        api_base: str = _DEFAULT_API_BASE,
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._http = http_client
        self._token: USPSToken | None = None
        self._token_lock = asyncio.Lock()
        self._rate_limiter = quota_guard
        api_base = api_base.rstrip("/")
        self._token_url = f"{api_base}{_TOKEN_PATH}"
        self._address_url = f"{api_base}{_ADDRESS_PATH}"

    @classmethod
    def _reset_recon_state(cls) -> None:
        """Clear the recon dedup set — test-only hook (issue #122)."""
        cls._recon_seen_signatures.clear()

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
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._consumer_key,
                    "client_secret": self._consumer_secret,
                },
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Token endpoint failures share the same operator-vs-outage
                # semantics as the address endpoint: 401/403 → bad-request
                # (creds bad), 5xx → transient (USPS outage).
                _raise_for_unexpected_status(exc, provider="usps", logger=logger)
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
        secondary_address: str | None = None,
    ) -> dict[str, Any]:
        """Validate a single US address via the USPS Addresses API v3.

        *secondary_address* carries the secondary-unit line (e.g. ``"LOT B"``,
        ``"APT 4"``) and is sent as the ``secondaryAddress`` query param when
        present; it is required for USPS to confirm the unit (GH #126).

        Retries up to :data:`~services.validation._rate_limit._RETRY_MAX` times
        on HTTP 429, honouring the ``Retry-After`` header when present and
        falling back to exponential backoff.  Raises
        :class:`~services.validation.errors.ProviderRateLimitedError` when all
        retries are exhausted.

        Returns a normalised dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``.

        Raises :class:`~services.validation.errors.ProviderBadRequestError`
        when the USPS API returns HTTP 400 (malformed input) or HTTP 401/403
        (operator action required: fix OAuth credentials).

        Raises :class:`~services.validation.errors.ProviderTransientError`
        on HTTP 5xx or any unexpected non-2xx response.
        """
        params: dict[str, str] = {"streetAddress": street_address}
        if secondary_address:
            # Secondary-unit line (e.g. "LOT B", "APT 4", "STE 200"). USPS v3
            # uses this for CASS secondary-match codes (DPV "S"/"D"); omitting it
            # silently drops the unit from the validated result (GH #126).
            params["secondaryAddress"] = secondary_address
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
                self._address_url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _HTTP_BAD_REQUEST:
                    logger.warning("USPSClient: 400 Bad Request from USPS API")
                    raise ProviderBadRequestError("usps", detail="HTTP 400") from exc
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
                _raise_for_unexpected_status(exc, provider="usps", logger=logger)

            raw: dict[str, Any] = resp.json()
            return self._map_response(raw)

        # unreachable — satisfies the type checker
        raise ProviderRateLimitedError("usps", retry_after_seconds=0.0)

    @classmethod
    def _log_recon_shape(cls, raw: dict[str, Any], dpv_label: str | None) -> None:
        """Log the structural shape of unsurfaced top-level fields.

        Reconnaissance for issue #122 (epic): the USPS v3 response includes
        ``corrections``, ``firm``, and ``matches`` which we currently
        discard. Before deciding whether/how to surface them we need to
        know what shapes they take across the DPV matrix (``Y``/``S``/``D``/
        ``N``/blank). Logs the structure (key names, length buckets, value
        types) of every top-level key outside :data:`_CONSUMED_TOP_LEVEL_KEYS`
        once per unique signature, paired with the normalised DPV code so
        we can cross-reference shape vs. match class.

        PII safety: ``_summarise_shape`` never emits scalar values. Only
        schema-level metadata reaches INFO logs — consistent with
        ``docs/LOGGING.md``.
        """
        extras = {k: v for k, v in raw.items() if k not in _CONSUMED_TOP_LEVEL_KEYS}
        if not extras:
            return
        shape = {k: _summarise_shape(v) for k, v in extras.items()}
        signature = f"dpv={dpv_label}|{sorted(shape.items())}"
        if signature in cls._recon_seen_signatures:
            return
        cls._recon_seen_signatures.add(signature)
        logger.info(
            "USPSClient recon: novel response shape (dpv=%s, extras=%r)",
            dpv_label,
            shape,
        )

    @staticmethod
    def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise the USPS v3 JSON response to a provider-neutral dict.

        Returns a flat dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``.
        """
        addr = raw.get("address", {})
        extra = raw.get("additionalInfo", {})

        dpv_label = _normalise_flag(extra.get("DPVConfirmation"))
        USPSClient._log_recon_shape(raw, dpv_label)

        zip_code = addr.get("ZIPCode", "")
        zip_ext = addr.get("ZIPPlus4", "") or ""
        postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code

        return {
            "dpv_match_code": dpv_label,
            "address_line_1": addr.get("streetAddress", ""),
            "address_line_2": addr.get("secondaryAddress", ""),
            "city": addr.get("city", ""),
            "region": addr.get("state", ""),
            "postal_code": postal_code,
            "vacant": _normalise_flag(extra.get("vacant")),
        }
