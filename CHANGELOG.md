# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses semantic versioning.

## [Unreleased]

### Added

- **New `validation.status` value: `undetermined`** ([#250](https://github.com/CannObserv/address-validator/issues/250)). A provider answered HTTP 200 but made no determination. The case that prompted it is USPS returning a blank `DPVConfirmation`. **Client-facing:** a client that handles every status explicitly will now receive a value it has not seen before. It is an answer about the address, so don't retry it. If the response also carries the new warning `Validation undetermined while a fallback provider was unreachable; a later retry may produce a determination`, the result was not cached and a retry may succeed. `unavailable` now means only that no provider is configured; outages have always been HTTP 429/5xx. Migration 021 widens `ck_validated_addresses_status`.
- **The provider chain falls back on `undetermined`** ([#250](https://github.com/CannObserv/address-validator/issues/250)). With `VALIDATION_PROVIDER=usps,google`, an address USPS cannot determine is now sent to Google. Google's answer replaces the USPS one only when it carries a DPV code ([#258](https://github.com/CannObserv/address-validator/issues/258)); a Google verdict with none (`confirmed`, `invalid` or `not_found` from Google's non-CASS path) is discarded and the USPS `undetermined` answer is returned, because Google's own guidance treats a US answer with no DPV code as needing a fix. Non-US answers never carry a DPV code, so this rule does not apply to them. Each such address costs one Google call on a cache miss, so bulk re-checks draw on `GOOGLE_DAILY_LIMIT`. If Google is rate-limited, over quota, erroring or unreachable, the USPS answer is still returned as a 200, with the retry warning.

- **`LOG_LEVEL` env var** ([#185](https://github.com/CannObserv/address-validator/issues/185)). App-logger verbosity is now configurable (default `INFO`); previously it was hardcoded, making the `DEBUG` events catalogued in `docs/LOGGING.md` unreachable without a code change. uvicorn's `--log-level` flag does *not* affect app loggers — it only reaches `uvicorn.error`/`uvicorn.access`/`uvicorn.asgi`.

### Fixed

- **DPV `D` and `S` now map to the right status** ([#253](https://github.com/CannObserv/address-validator/issues/253)). Per the USPS spec, `D` means the unit is missing and `S` means a unit was supplied but not confirmed; the two statuses were swapped for both USPS and Google answers. **Client-facing:** the same address now returns `confirmed_missing_secondary` where it returned `confirmed_bad_secondary`, and vice versa; `dpv_match_code` itself is unchanged. Migration 022 relabels cached rows from their stored DPV code, so cache hits are correct from the deploy on. `audit_log` is not rewritten: rows before the cutover read correctly by swapping the two labels, and the admin provider view mixes both meanings until they age out. Cutover recorded in `docs/VALIDATION-STATUS.md`.
- **USPS "no determination" is no longer reported as `unavailable`** ([#250](https://github.com/CannObserv/address-validator/issues/250)). The docs defined `unavailable` as "provider not configured or unreachable", but USPS returned it for some addresses on every call: it was never cached, never tried against Google, and read by clients as an outage. wslcb-licensing-tracker's backfill stalled on it. Since 2026-07-02, 362 distinct inputs got this answer; in July it made up 2,120 of 2,735 uncached USPS calls.
- **A cached answer is refreshed when a re-validation changes it** ([#250](https://github.com/CannObserv/address-validator/issues/250)). The cache key hashes address fields only, so an answer that changed while its fields stayed the same (for example `undetermined` ↔ `not_confirmed`, both with a bare ZIP5) kept serving the old status until the next TTL. The upsert now refreshes status, DPV, provider and the other answer columns.
- **An unrecognised or blank Google `dpvConfirmation` no longer fails response validation** ([#250](https://github.com/CannObserv/address-validator/issues/250)); the code is dropped and the status is `undetermined`. A closer look is tracked in [#254](https://github.com/CannObserv/address-validator/issues/254).

- **Canadian address content no longer written to logs at `INFO`** ([#185](https://github.com/CannObserv/address-validator/issues/185)). The libpostal sidecar is called as `GET /parse?address=<address>`, and `httpx` logs the full request URL at `INFO` — so every CA request emitted the user's address verbatim, contrary to the no-PII-at-INFO+ rule in `AGENTS.md`. `httpx` and `httpcore` are now pinned to `WARNING` independently of `LOG_LEVEL`, so raising verbosity cannot reopen it. Pre-dates the JSON logging work; found during review of it.

- **Audit-log archive runs again, and archives whole days** ([#228](https://github.com/CannObserv/address-validator/issues/228)). The installed `audit-archive` and `docker-prune` units still pointed at pre-#109 `scripts/` paths and failed every night, unnoticed. The archiver also cut days at the 03:00 timer time rather than UTC midnight, so a day's rollup and Parquet file would have lost its 00:00–03:00 slice; it now exits instead of deleting when `AUDIT_ARCHIVE_BUCKET` is unset, and streams the export (298k-row day: 648 MB → 153 MB peak RSS). No address-derived column is archived (`raw_input`, `pattern_key`); `client_ip` is archived raw (HMAC tracked in [#233](https://github.com/CannObserv/address-validator/issues/233)).

- **Service no longer exits at boot while the libpostal sidecar warms up** ([#239](https://github.com/CannObserv/address-validator/issues/239)). Docker's port proxy accepts the connection before the container is ready, then drops it (`httpx.RemoteProtocolError`), which the client did not catch: startup exited and relied on `Restart=on-failure` (~6 s of the 2026-09-29 boot outage), `GET /api/v2/health` returned 500 instead of `libpostal: "unavailable"`, and CA parse/standardize/validate returned 500 instead of the designed 503. Every request-level failure (`httpx.RequestError`), plus a non-JSON or malformed sidecar body, now maps to "unavailable": boot proceeds degraded with a WARNING, health stays 200, CA requests get 503.

- **libpostal boot WARNING now says why the sidecar was unreachable** ([#244](https://github.com/CannObserv/address-validator/issues/244)). The line reads `libpostal sidecar not reachable at <url> (<reason>) — …`, where the reason is the exception class (`RemoteProtocolError`, `ConnectError`, `ReadTimeout`, `RuntimeError`) or `HTTP <status>`. Previously a warmup disconnect was indistinguishable from a refused connect or timeout. New `LibpostalClient.probe()` supplies it; `health_check()` keeps its `bool` contract and still logs nothing per poll.

### Added (operations)

- **`infra/install-units.sh`** ([#228](https://github.com/CannObserv/address-validator/issues/228)) — the single install path for `infra/*.service` + `*.timer` (copy, `daemon-reload`, `reset-failed`; refuses a linked worktree); `--check` reports drift between installed units and `infra/`.
- **Timer-driven unit failures reach the journal** ([#228](https://github.com/CannObserv/address-validator/issues/228)) — `OnFailure=unit-failure@%n.service` logs one `crit` line per failure: `journalctl -t unit-failure`. Now also pushed through notifier; see the #232 entry below.
- **Timer-driven unit failures are pushed to Slack and Mailgun via notifier** ([#232](https://github.com/CannObserv/address-validator/issues/232)).
  - `unit-failure@.service` keeps the `crit` journal line as its first step, then runs `infra/notify_unit_failure.py`.
  - The dispatch carries the unit, its result, the host, and the failed run's last WARNING-or-higher journal lines.
  - It is fail-open: if notifier is unreachable or rejects the call, or the config is incomplete, the journal line stays the only signal. With `NOTIFIER_URL` unset, nothing is dispatched.
  - The host joined the `cannobserv.org.github` tailnet, and Tailscale now owns DNS.
  - New dependency: `notifier-client`, pinned to git tag `v0.3.1`. Run `uv sync` in the main checkout before installing the unit.
  - `infra/sweep_cache.py` and `infra/archive_audit.py` now log with real journal priority when run under systemd. `journalctl -p warning` sees their `ERROR`/`WARNING` lines, which were previously all filed at info.

- **Crashloops of the API and the libpostal sidecar alert once** ([#248](https://github.com/CannObserv/address-validator/issues/248)).
  - `address-validator.service` and `libpostal.service` carry `OnFailure=unit-failure@%n.service`, `RestartMode=direct`, and explicit start limits (10 / 600s and 5 / 600s).
  - **Behaviour change:** a crashloop now stops at the limit, alerts through notifier, and **stays down** until `systemctl reset-failed` + `start`. Previously both restarted forever, silently. Manual restarts count toward the limit.
  - `RestartMode=direct` is what keeps it to one alert. Under the default, every crash fires `OnFailure=`, one dispatch per restart.

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
