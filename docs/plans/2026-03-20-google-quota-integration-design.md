# Google Cloud Quota Integration Design

**Date:** 2026-03-20

## Problem

On restart, QuotaGuard initializes daily token buckets to full capacity with no
awareness of actual usage. If the service restarts mid-day after consuming most
of its quota, it optimistically allows requests that Google will reject with
429s.

## Solution

Integrate Cloud Quotas API (limits) and Cloud Monitoring API (usage) to:

1. Auto-discover quota ceilings at boot instead of relying on env vars
2. Seed daily token buckets with actual remaining usage on boot
3. Periodically reconcile local tracking against Cloud Monitoring (daily window only)
4. Consolidate auth from API key to ADC (service account)

## Auth Consolidation

- **Remove** `GOOGLE_API_KEY` env var
- **Add** ADC-based auth via `google-auth` — used for Address Validation, Cloud
  Quotas, and Cloud Monitoring APIs
- **Add** `GOOGLE_PROJECT_ID` env var (optional) — falls back to auto-discovery
  from ADC credentials (service account JSON `project_id` field or GCE metadata
  server)
- IAM roles required on the service account:
  - `roles/addressvalidation.user`
  - `roles/cloudquotas.viewer`
  - `roles/monitoring.viewer`

## Env Var Changes

### New

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_PROJECT_ID` | No | Auto-discovered from ADC | GCP project ID |
| `GOOGLE_QUOTA_RECONCILE_INTERVAL_S` | No | `900` | Seconds between reconciliation checks |

### Removed

| Variable | Replacement |
|---|---|
| `GOOGLE_API_KEY` | ADC credentials |
| `GOOGLE_DAILY_LIMIT` | Auto-discovered from Cloud Quotas API (env var kept as optional override) |

## Daily Window: Rolling to Fixed Wall-Clock Reset

- **Google-specific change** — USPS keeps its rolling `QuotaWindow`
- New class `FixedResetQuotaWindow` (or similar) that resets to full capacity at
  midnight Pacific Time
- `QuotaGuard` updated to support mixed window types (rolling per-minute +
  fixed-reset daily)
- Uses `zoneinfo` (stdlib 3.9+) for `America/Los_Angeles` timezone

## Boot Sequence

```
startup
 +- Load ADC credentials (google-auth)
 +- Resolve project ID (env var -> ADC -> metadata server)
 +- Query Cloud Quotas API -> get daily limit for addressvalidation.googleapis.com
 |   +- Success -> use as daily ceiling
 |   +- Failure -> log warning, fall back to GOOGLE_DAILY_LIMIT env var or default 160
 +- Query Cloud Monitoring API -> get today's usage count
 |   +- Success -> seed daily bucket at (limit - usage)
 |   +- Failure -> log warning, start with full bucket (current behavior)
 +- Start reconciliation background task
```

## Reconciliation (every 15 min, daily window only)

```
reconcile_tick
 +- Query Cloud Monitoring -> reported_usage
 +- Compute local_usage = daily_limit - current_tokens
 +- delta = reported_usage - local_usage
 +- if delta > 0:  # Google says we used more than we think
 |    adjust tokens down by delta
 |    log WARNING "quota drift detected, adjusted down by {delta}"
 +- elif delta < 0:  # Google says we used less (likely lag)
 |    log WARNING "quota drift: local={local_usage} monitoring={reported_usage}, not adjusting up"
 +- else: no-op
```

- **Per-minute window**: never reconciled — local tracking authoritative
- **Per-day window**: only adjusts downward, never upward
- Drift within what ~10 min of traffic could explain: log at DEBUG, not WARNING

## Cloud Quotas API Call (boot only)

```
GET /v1/projects/{project}/locations/global/services/addressvalidation.googleapis.com/quotaInfos

-> Find QuotaInfo where refreshInterval == "day"
-> Extract dimensionsInfos[0].details.value -> daily limit
```

## Cloud Monitoring API Call (boot + periodic)

```
POST /v3/projects/{project}/timeSeries:query

Filter: metric.type = "serviceruntime.googleapis.com/quota/allocation/usage"
        resource.type = "consumer_quota"
        resource.label.service = "addressvalidation.googleapis.com"
Period: midnight PT -> now
```

## New Modules

```
src/address_validator/services/validation/
 +- gcp_auth.py           # ADC credential loading, project ID resolution
 +- gcp_quota_sync.py     # Cloud Quotas + Monitoring API calls, reconciliation loop
 +- _rate_limit.py        # Add FixedResetQuotaWindow alongside existing QuotaWindow
```

## Changes to Existing Modules

| File | Change |
|---|---|
| `factory.py` | Replace API key config with ADC; call quota sync at provider creation; wire reconciliation task |
| `google_client.py` | Switch from API key auth to ADC bearer token on HTTP requests |
| `_rate_limit.py` | Add `FixedResetQuotaWindow`, add `adjust_tokens(window_index, delta)` method to `QuotaGuard` |
| `main.py` | Start/stop reconciliation background task in lifespan |

## Failure Modes

| Scenario | Behavior |
|---|---|
| ADC credentials missing/invalid | Startup fails (Address Validation can't work without auth) |
| Cloud Quotas API unavailable | Warn, fall back to env var / default limit. Service starts. |
| Cloud Monitoring API unavailable | Warn, start with full bucket. Service starts. |
| Reconciliation call fails at runtime | Log error, skip this tick, retry next interval |
| Project ID not resolvable | Warn, quota features disabled, use env var defaults |

## Dependencies

| Package | Purpose |
|---|---|
| `google-cloud-quotas` | Cloud Quotas API client |
| `google-cloud-monitoring` | Cloud Monitoring API client |
| `google-auth` | ADC credential loading (transitive dep of above) |

## Test Strategy

- **Unit**: Mock GCP client responses for quota sync logic; test
  `FixedResetQuotaWindow` reset behavior at simulated midnight PT boundaries;
  test `adjust_tokens` method
- **Unit**: Mock ADC for `gcp_auth.py`; test project ID fallback chain
- **Integration**: Factory wiring with mocked GCP clients; boot sequence with
  various failure combinations
- **Existing tests**: Update to remove `GOOGLE_API_KEY` references, mock ADC
  instead
