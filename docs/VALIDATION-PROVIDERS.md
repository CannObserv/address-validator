# Validation Providers

## DPV status mapping

| DPV code | `validation.status` | Meaning |
|---|---|---|
| `Y` | `confirmed` | Fully confirmed delivery point |
| `S` | `confirmed_missing_secondary` | Building confirmed; unit/apt missing |
| `D` | `confirmed_bad_secondary` | Building confirmed; unit not recognised |
| `N` | `not_confirmed` | Address not found in USPS database |
| (none) | `unavailable` | Provider not configured or unreachable |

## USPS provider

- API: USPS Addresses API v3. Spec archived at `docs/usps-addresses-v3r2_3.yaml`.
- Auth: OAuth2 client credentials. Token cached 55 min in-process (`asyncio.Lock` prevents concurrent refresh races).
- Rate limit: token bucket, 5 req/s (free-tier limit). 10,000 validations/day.
- Register at https://developer.usps.com.
- `USPSProvider` and its `_http_client` are module-level singletons in `factory.py` — reset in tests.

## Google provider

- API: Google Address Validation API. Single POST with `enableUspsCass: true`.
- Auth: API key (`GOOGLE_API_KEY`). No OAuth2 or rate limiter.
- Populates `latitude`/`longitude`. Surfaces three verdict flags as warnings.
- `GoogleProvider` is a module-level singleton in `factory.py` — reset in tests.

## Notes

- See `docs/usps-pub28.md` for USPS Pub 28 edition notes and spec version pinning.
- `factory.py` singletons: `_usps_provider`, `_google_provider`, `_http_client`. Tests must reset to `None` in a fixture.
