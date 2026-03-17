# Design: Provider Rate Limits, Retry/Backoff, and Fallback Chain

**Date:** 2026-03-17
**Status:** Approved

## Problem

The service calls external validation APIs but currently has gaps in rate-limit compliance and resilience:

- Google has no rate limiter (USPS has a hardcoded 5 req/s token bucket)
- Neither provider retries or backs off on HTTP 429
- Only one provider can be configured at a time — no fallback when rate-limited
- Rate limits are hardcoded; operators on different USPS/Google plans cannot tune them

## Non-goal: Dynamic quota querying

Both USPS and Google were investigated for real-time quota/usage APIs:
- **USPS:** No such endpoint exists at the API level
- **Google:** Quota data is in Cloud Monitoring (separate GCP API, separate credentials, added latency per request)

Conclusion: client-side token buckets + 429-detection is the right approach. Dynamic querying adds complexity with marginal benefit.

## Design

### New: `services/validation/_rate_limit.py`

Shared module extracted from `usps_client.py` and extended:

- `_TokenBucket(rate, capacity)` — async token-bucket rate limiter (moved from `usps_client.py`)
- `_parse_retry_after(response, attempt) -> float` — reads `Retry-After` header value (seconds); falls back to exponential backoff `base * 2^attempt + jitter` when header is absent
- Constants: `_RETRY_MAX = 3`, `_RETRY_BASE_DELAY_S = 1.0`

### New: `services/validation/errors.py`

```python
class ProviderRateLimitedError(Exception):
    """Raised by a provider client after all 429 retries are exhausted."""
    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"Provider '{provider}' rate-limited after retries")
```

`ChainProvider` catches this to try the next provider. The router catches it when all providers raise it → HTTP 503.

### Retry loop in both clients

`USPSClient.validate_address` and `GoogleClient.validate_address` gain:

```python
for attempt in range(_RETRY_MAX + 1):
    await self._rate_limiter.acquire()
    try:
        resp = await self._http.<method>(...)
        resp.raise_for_status()
        return self._map_response(resp.json())
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429 and attempt < _RETRY_MAX:
            await asyncio.sleep(_parse_retry_after(exc.response, attempt))
        elif exc.response.status_code == 429:
            raise ProviderRateLimitedError("<name>") from exc
        else:
            raise
```

### Configurable rate limits

Both clients accept `rate_limit_rps: float` (defaults: USPS=5.0, Google=25.0).

New env vars (read by factory):

| Variable | Default | Notes |
|---|---|---|
| `USPS_RATE_LIMIT_RPS` | `5.0` | Matches USPS free-tier documented limit |
| `GOOGLE_RATE_LIMIT_RPS` | `25.0` | Matches Google Address Validation default quota |

### New: `services/validation/chain_provider.py`

```python
class ChainProvider:
    def __init__(self, providers: list[ValidationProvider]) -> None: ...

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        for provider in self._providers:
            try:
                return await provider.validate(std)
            except ProviderRateLimitedError:
                logger.warning("provider rate-limited, trying next", ...)
        raise ProviderRateLimitedError("all")
```

Satisfies the `ValidationProvider` protocol.

### Factory changes

`VALIDATION_PROVIDER` now accepts a comma-separated ordered list:

| Value | Behavior |
|---|---|
| `none` (or empty) | NullProvider — unchanged |
| `usps` | USPSProvider only — unchanged |
| `google` | GoogleProvider only — unchanged |
| `usps,google` | USPS primary, Google fallback |
| `google,usps` | Google primary, USPS fallback |

Single providers continue to work without a chain wrapper. Multiple providers are wrapped in `ChainProvider`. The `CachingProvider` wraps the resolved result (chain or single) as before.

New env vars `USPS_RATE_LIMIT_RPS` and `GOOGLE_RATE_LIMIT_RPS` are read and passed to client constructors.

### Router: HTTP 503

```python
from services.validation.errors import ProviderRateLimitedError

try:
    result = await provider.validate(std)
except ProviderRateLimitedError:
    raise APIError(
        status_code=503,
        error="provider_rate_limited",
        message="All configured validation providers are currently rate-limited. Retry later.",
    )
```

503 added to `responses=` dict on the endpoint.

## Files changed

| File | Change |
|---|---|
| `services/validation/_rate_limit.py` | **NEW** |
| `services/validation/errors.py` | **NEW** |
| `services/validation/chain_provider.py` | **NEW** |
| `services/validation/usps_client.py` | Shared `_TokenBucket`; `rate_limit_rps` param; retry loop |
| `services/validation/google_client.py` | Add `_TokenBucket`; `rate_limit_rps` param; retry loop |
| `services/validation/factory.py` | Comma-sep provider list; rate-limit env vars; `ChainProvider` |
| `routers/v1/validate.py` | Catch `ProviderRateLimitedError` → 503 |
| `AGENTS.md` | New env vars; updated `VALIDATION_PROVIDER` description |
| `docs/VALIDATION-PROVIDERS.md` | Chain syntax; rate-limit env vars |
| `tests/` | Tests for all new modules and router 503 |
