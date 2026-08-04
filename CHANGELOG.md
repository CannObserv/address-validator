# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses semantic versioning.

## [Unreleased]

### Changed

- **Logs are now structured JSON** ([#185](https://github.com/CannObserv/address-validator/issues/185)). Every line on stdout (and therefore in journald) is a JSON object with `{timestamp, level, logger, message, request_id}`, replacing the previous `LEVEL:name:message` text format — which carried no timestamp and silently dropped the `request_id` ULID. uvicorn's own `uvicorn`/`uvicorn.access`/`uvicorn.error` loggers share the same formatter via `--log-config src/address_validator/core/log_config.json`, so access lines are JSON too and are correlated by `request_id`. Operators grepping journald for the old plain-text shape must update; see [`docs/LOGGING.md`](docs/LOGGING.md).

### Changed (breaking)

- **CA `POST /api/v2/parse` now reports its true source spec** ([#134](https://github.com/CannObserv/address-validator/issues/134)). For Canadian addresses, `components.spec` in the parse response is now `raw` (the honest spec for an unstandardized libpostal parse) instead of being silently relabelled `iso-19160-4`. This converges `parse` onto the same spec-selection rule `standardize` already used. Consumers keying on the parse-CA `components.spec` value must update; `standardize` CA responses are unchanged (`canada-post`).
- **Response warning string spelling normalized to American English** ([#131](https://github.com/CannObserv/address-validator/issues/131)). The standardize warning `"Unrecognised province/territory: '...'"` is now `"Unrecognized province/territory: '...'"`. Consumers matching this string in the `warnings` channel must update. All response-warning strings are now defined in `core/warnings.py` and documented in the living catalogue [`docs/WARNINGS.md`](docs/WARNINGS.md); a drift test fails CI if the two diverge.

## [3.0.0] — 2026-06-03

### Removed (breaking)

- **`/api/v1/*` API surface** ([#117](https://github.com/CannObserv/address-validator/issues/117)). The v1 routers (`parse`, `standardize`, `validate`, `countries`, `health`) are deleted; calls to those paths return HTTP 404. The geography-neutral `/api/v2/*` surface (live since 2026-04-08) is the only public API.
  - The two known external consumers (`power-map`, `wslcb-licensing-tracker`) migrated to v2 in their respective repos prior to this release.
  - Internal scripts (`scripts/model/deploy.py` smoke-test, `scripts/model/performance.py` benchmark) retargeted at `/api/v2/*` in commit [`cdc7382`](https://github.com/CannObserv/address-validator/commit/cdc7382).
- **`api_version` field on `ErrorResponse`** dropped. Prior to this release, v2 error bodies were silently emitting `"api_version": "1"` (inherited from the shared v1 model). The new shape is `{"error": "...", "message": "..."}`; clients should read the version from the `API-Version: 2` response header instead.
- **Public Pydantic models** `ParseResponseV1`, `StandardizeResponseV1`, `ValidateResponseV1`, the v1 `HealthResponse`, and the v1 `CountryFormatResponse` are removed.
- **`run_non_us_pipeline_v1`** removed from `services/validation/pipeline.py` (dead code after the v1 router deletion).

### Changed

- `ParseRequestV1` / `StandardizeRequestV1` / `ValidateRequestV1` renamed to `ParseRequest` / `StandardizeRequest` / `ValidateRequest` — they are version-neutral request models reused by the v2 routers.
- `StandardizedAddress` type alias now points at `StandardizeResponseV2` (was `StandardizeResponseV1`).
- Validation providers (`USPSProvider`, `GoogleProvider`, `NullProvider`, `CacheProvider`) and the `services/validation/pipeline.py` helpers construct `ValidateResponseV2` directly with empty-string defaults for top-level address fields. The internal `_v1_to_v2` adapter in `routers/v2/validate.py` is removed.
- `ApiVersionHeaderMiddleware` stamps `API-Version: 2` only; it no longer matches `/api/v1/*` paths.
- Audit-middleware invariant set (`_VALIDATE_ENDPOINTS`) narrows to `{"/api/v2/validate"}`.

### Preserved

- The admin dashboard endpoint and audit queries continue to filter on both `/api/v1/*` and `/api/v2/*` paths, so the 494k+ historical audit_log rows generated before the v1 cutover remain visible alongside fresh v2 traffic.
