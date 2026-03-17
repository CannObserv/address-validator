# Validation Providers

## DPV status mapping

| DPV code | `validation.status` | Meaning |
|---|---|---|
| `Y` | `confirmed` | Fully confirmed delivery point |
| `S` | `confirmed_missing_secondary` | Building confirmed; unit/apt missing |
| `D` | `confirmed_bad_secondary` | Building confirmed; unit not recognised |
| `N` | `not_confirmed` | Address not found in USPS database |
| (none) | `unavailable` | Provider not configured or unreachable |

## Configuring providers

Set `VALIDATION_PROVIDER` in `/etc/address-validator/env`:

```
# Single provider
VALIDATION_PROVIDER=usps

# Fallback chain — USPS primary, Google secondary
VALIDATION_PROVIDER=usps,google
```

When a provider is rate-limited (HTTP 429 after all retries), the next provider in the comma-separated list is tried. If all providers are exhausted, the `/api/v1/validate` endpoint returns HTTP 429 with a `Retry-After` header.

## USPS provider

- API: USPS Addresses API v3. Spec archived at `docs/usps-addresses-v3r2_3.yaml`.
- Auth: OAuth2 client credentials. Token cached 55 min in-process (`asyncio.Lock` prevents concurrent refresh races).
- Rate limit: token bucket, default 5 req/s (free-tier limit). Configurable via `USPS_RATE_LIMIT_RPS`.
- 429 retry: up to 3 retries with `Retry-After` header support; falls back to exponential backoff (1 s base, ×2 per attempt + jitter).
- Register at https://developer.usps.com.
- `USPSProvider` and its `_http_client` are module-level singletons in `factory.py` — reset in tests.

## Google provider

- API: Google Address Validation API. Single POST with `enableUspsCass: true`.
- Auth: API key (`GOOGLE_API_KEY`).
- Rate limit: token bucket, default 25 req/s (standard per-project quota). Configurable via `GOOGLE_RATE_LIMIT_RPS`.
- 429 retry: same retry/backoff policy as USPS (up to 3 retries, `Retry-After` + exponential backoff).
- Populates `latitude`/`longitude`. Surfaces three verdict flags as warnings.
- `GoogleProvider` is a module-level singleton in `factory.py` — reset in tests.

## Rate limit env vars

| Variable | Default | Notes |
|---|---|---|
| `USPS_RATE_LIMIT_RPS` | `5.0` | Matches USPS free-tier documented limit |
| `GOOGLE_RATE_LIMIT_RPS` | `25.0` | Matches Google Address Validation default quota |

## Dynamic quota querying

Neither USPS nor Google expose real-time quota/usage data through their validation APIs. USPS has no such endpoint. Google's quota data is in Cloud Monitoring — a separate GCP API requiring different credentials. Client-side token buckets and 429 detection are the reliable mechanism.

## Fallback chain internals

`ChainProvider` (`services/validation/chain_provider.py`) wraps a list of providers. It catches `ProviderRateLimitedError` (raised by a client after all 429 retries) and delegates to the next provider. All other exceptions (network errors, 5xx) propagate immediately. The `CachingProvider` wraps the chain, so a cache hit bypasses all providers in the chain.

## Notes

- See `docs/usps-pub28.md` for USPS Pub 28 edition notes and spec version pinning.
- `factory.py` singletons: `_usps_provider`, `_google_provider`, `_http_client`, `_caching_provider`. Tests must reset to `None` in a fixture.
