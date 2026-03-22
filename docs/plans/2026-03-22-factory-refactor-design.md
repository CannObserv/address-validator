# Factory Refactor — Config Dataclass + ProviderRegistry + Google Wiring Extraction

**Issue:** #50
**Date:** 2026-03-22

## Problem

`factory.py` is 441 lines with 5 `global` declarations and interleaved concerns:
env-var parsing, provider construction, HTTP client lifecycle, quota discovery,
and reconciliation parameter assembly. Module-level singletons create hidden
coupling and fragile test fixtures (manual reset of 5 globals).

## Decisions

| Decision | Choice |
|---|---|
| Scope | All three: config dataclass + registry + Google wiring extraction |
| Config layer | pydantic-settings (`BaseSettings` subclasses) |
| Quota API | Public `get_quota_info()` method on registry |
| File layout | Delete `factory.py`; new `config.py` + `registry.py` |
| Google wiring | Private methods on `ProviderRegistry` |

## Design

### 1. `validation/config.py` — pydantic-settings models

Three settings classes reading from env vars:

- **`USPSConfig`** (`env_prefix="USPS_"`): `consumer_key`, `consumer_secret`,
  `rate_limit_rps` (float >= 1, default 5.0), `daily_limit` (int > 0, default 10000)
- **`GoogleConfig`** (`env_prefix="GOOGLE_"`): `project_id` (optional),
  `rate_limit_rpm` (int > 0, default 5), `daily_limit` (int > 0, default 160),
  `quota_reconcile_interval_s` (float > 0, default 900.0)
- **`ValidationConfig`** (`env_prefix="VALIDATION_"`): `provider` (str, default "none"),
  `latency_budget_s` (float > 0, default 1.0), `cache_dsn` (str, default ""),
  `cache_ttl_days` (int >= 0, default 30)

Custom `field_validator`s enforce the same business rules currently in `_parse_*` functions.
`validate_config()` becomes: construct settings models (pydantic raises on bad env) +
check `cache_dsn` is set when non-null provider is configured.

### 2. `validation/registry.py` — ProviderRegistry class

```
class ProviderRegistry:
    __init__(config: ValidationConfig, usps_config, google_config)
    get_provider() -> ValidationProvider          # lazy singleton
    get_reconciliation_params() -> dict | None
    get_quota_info() -> list[dict]                # public API for admin
    close() -> None                               # cleanup http client

    # Private
    _build_provider() -> ValidationProvider
    _get_http_client() -> httpx.AsyncClient
    _build_usps_provider(cfg, latency_budget) -> USPSProvider
    _build_google_provider(cfg, latency_budget) -> GoogleProvider
    _discover_google_quota(credentials, project_id, cfg) -> int
    _setup_reconciliation(guard, credentials, project_id, cfg) -> None
```

No `global` statements. State lives on the instance. Single instance created in
`main.py` lifespan, stored on `app.state.registry`.

### 3. Lifespan changes (`main.py`)

```python
async def lifespan(app):
    config = ValidationConfig()
    registry = ProviderRegistry(config)
    registry.get_provider()          # eagerly construct + wire quota sync
    app.state.registry = registry
    # start reconciliation task from registry.get_reconciliation_params()
    yield
    await registry.close()
    await close_engine()
```

### 4. Import updates

| File | Change |
|---|---|
| `main.py` | Import from `validation.config` + `validation.registry` |
| `routers/v1/validate.py` | Get registry from `request.app.state.registry` |
| `routers/admin/_config.py` | Call `registry.get_quota_info()` instead of poking at factory globals |
| `factory.py` | Deleted |

### 5. Test impact

- `reset_singletons` fixture replaced by constructing a fresh `ProviderRegistry` per test
- Tests can construct config objects directly or via `monkeypatch.setenv`
- No globals to reset = no cross-test contamination risk

### 6. Google wiring extraction

`_build_google_provider` on ProviderRegistry delegates to:
- `_discover_google_quota(credentials, project_id, google_cfg)` — Cloud Quotas API call, returns daily_limit
- `_setup_reconciliation(guard, credentials, project_id, google_cfg)` — sets `self._reconciliation_params`

## Out of scope

- Changing provider protocol or adding new providers
- Modifying rate-limit or caching behavior
- Changing the API contract
