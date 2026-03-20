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
- Rate limit: multi-window quota guard — per-second soft window (default 5 req/s), per-day soft
  window (default 10 000/day). Configurable via `USPS_RATE_LIMIT_RPS` and `USPS_DAILY_LIMIT`.
- 429 retry: up to 3 retries with `Retry-After` header support; falls back to exponential backoff (1 s base, ×2 per attempt + jitter).
- Register at https://developer.usps.com.
- `USPSProvider` and its `_http_client` are module-level singletons in `factory.py` — reset in tests.

## Google provider

- API: Google Address Validation API. Single POST with `enableUspsCass: true`.
- Auth: Application Default Credentials (ADC) — no API key. Required IAM roles:
  - `roles/addressvalidation.user` — call the Address Validation API
  - `roles/cloudquotas.viewer` — read quota limits at startup
  - `roles/monitoring.viewer` — read current usage from Cloud Monitoring
- Rate limit: multi-window quota guard — per-minute soft window (default 5 req/min), per-day
  window with fixed midnight PT reset (not rolling 86400 s). Daily limit optional — auto-discovered
  from Cloud Quotas API if `GOOGLE_DAILY_LIMIT` is unset. Configurable via `GOOGLE_RATE_LIMIT_RPM`
  and `GOOGLE_DAILY_LIMIT`.
- 429 retry: same retry/backoff policy as USPS (up to 3 retries, `Retry-After` + exponential backoff).
- Populates `latitude`/`longitude`. Surfaces three verdict flags as warnings.
- `GoogleProvider` is a module-level singleton in `factory.py` — reset in tests.

## Rate limit and quota env vars

| Variable | Default | Notes |
|---|---|---|
| `USPS_RATE_LIMIT_RPS` | `5.0` | USPS per-second window limit |
| `USPS_DAILY_LIMIT` | `10000` | USPS per-day window limit (soft — queues up to latency budget) |
| `GOOGLE_PROJECT_ID` | — | GCP project ID; optional, auto-discovered from ADC if unset |
| `GOOGLE_RATE_LIMIT_RPM` | `5` | Google per-minute window limit (soft) |
| `GOOGLE_DAILY_LIMIT` | `160` | Google per-day limit; auto-discovered from Cloud Quotas API when available, env var is fallback |
| `GOOGLE_QUOTA_RECONCILE_INTERVAL_S` | `900` | Seconds between periodic quota reconciliation runs |
| `VALIDATION_LATENCY_BUDGET_S` | `1.0` | Max seconds a request may queue before overflowing to the next provider |

## Cache TTL

`VALIDATION_CACHE_TTL_DAYS` (default `30`) — days after which a cached result is re-validated via the live provider. Set to `0` to disable expiry (entries live indefinitely; backward-compatible opt-out).

The TTL is checked against `validated_addresses.validated_at`, which records when a live provider last returned and stored this canonical result. This timestamp is **not** refreshed by cache hits — a frequently-queried entry still expires after `VALIDATION_CACHE_TTL_DAYS` days.

`last_seen_at` continues to track query frequency for observability and is unrelated to expiry.

**Schema migrations**: Managed by Alembic. `get_engine()` runs `alembic upgrade head` automatically on first call at startup. To migrate data from a prior SQLite cache, run `scripts/migrate_sqlite_to_postgres.py` after applying migrations.

## Dynamic quota querying

USPS has no real-time quota endpoint — client-side quota guards and 429 detection are the only mechanism.

Google quota is reconciled dynamically via two GCP APIs:
- **Cloud Quotas API** — reads the current daily limit for the Address Validation API quota dimension
- **Cloud Monitoring API** — reads cumulative usage for the current calendar day (midnight PT reset)

**Boot sequence:** on provider construction, `gcp_quota_sync.py` calls Cloud Quotas to discover the
daily limit (sets `GOOGLE_DAILY_LIMIT` if not overridden), then calls Cloud Monitoring to seed the
current usage into `FixedResetQuotaWindow` so the guard starts with the correct remaining tokens.

**Periodic reconciliation:** `run_reconciliation_loop()` runs as a background asyncio task, invoking
`reconcile_once()` every `GOOGLE_QUOTA_RECONCILE_INTERVAL_S` seconds (default 900). Each run
re-reads Cloud Monitoring usage and calls `adjust_tokens()` on the daily window. Reconciliation only
adjusts tokens **downward** — it never grants additional tokens beyond what the window's own refill
logic would allow. The task is cancelled on application shutdown.

## Fallback chain internals

`ChainProvider` catches both `ProviderRateLimitedError` (upstream HTTP 429 after retries) and
`ProviderAtCapacityError` (local quota exhausted before sending) and delegates to the next
provider.

## Notes

- See `docs/usps-pub28.md` for USPS Pub 28 edition notes and spec version pinning.
- `factory.py` singletons: `_usps_provider`, `_google_provider`, `_http_client`, `_caching_provider`. Tests must reset to `None` in a fixture.
