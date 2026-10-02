# Validation Status Vocabulary

Authoritative, living catalogue of every value that may appear in a
`ValidationResult.status` field. Consumers parsing `validation.status` should
treat this table as the reference for the values they may receive.

**Source of truth:** all values are defined in
[`core/validation_status.py`](../src/address_validator/core/validation_status.py).
This document and that module — plus the `ValidationResult.status` `Literal`
([`models.py`](../src/address_validator/models.py)), the
`validated_addresses.status` `CheckConstraint`
([`db/tables.py`](../src/address_validator/db/tables.py)), the DPV→status map
([`services/validation/_helpers.py`](../src/address_validator/services/validation/_helpers.py)),
and the admin `VS_META` table
([`routers/admin/_config.py`](../src/address_validator/routers/admin/_config.py)) —
are kept in sync by the drift test
[`tests/unit/test_validation_status_catalogue.py`](../tests/unit/test_validation_status_catalogue.py),
which fails CI if any side gains or loses an entry. It also requires the DPV
column below, and the DPV table in
[`VALIDATION-PROVIDERS.md`](VALIDATION-PROVIDERS.md), to match the DPV→status
map. To add or change a status, edit `core/validation_status.py` and this table
together.

This mirrors the response-warning catalogue pattern
([`WARNINGS.md`](WARNINGS.md), GH #131/#132).

## Catalogue

| Status | DPV code | Meaning |
|---|---|---|
| `confirmed` | Y | Fully confirmed delivery point. For non-US input (no DPV code), a Google verdict with `addressComplete`. A US answer without a DPV code is never `confirmed` (GH #262). |
| `confirmed_missing_secondary` | D | Building confirmed, unit (secondary) missing. |
| `confirmed_bad_secondary` | S | Building confirmed, unit (secondary) supplied but not confirmed. |
| `not_confirmed` | N | Address not found in the USPS database. |
| `not_found` | — | Google verdict (non-US, or US without a CASS DPV code): address could not be geocoded or verified. |
| `invalid` | — | Google verdict (non-US, or US without a CASS DPV code): address is geocodable but incomplete. |
| `undetermined` | — | A provider answered (HTTP 200) but made no determination — e.g. USPS returned no DPV code. Google's US answer is also `undetermined` when its verdict is complete but CASS returned no DPV code (GH #262). An answer about the address, not an outage: retrying returns the same answer. When a fallback provider was unreachable, the response carries a warning and a retry may yield a determination (GH #250). For a US address it is also returned when the fallback answered without a DPV code: that answer is discarded, not merged (GH #258). |
| `unavailable` | — | No validation provider is configured. Never a per-address outcome; an outage surfaces as HTTP 429/5xx, not as a status. |
| `error` | — | Provider rejected the input as malformed. |

## D/S correction (GH #253)

Until GH #253, DPV `D` and `S` were mapped to each other's status, against the
USPS spec ([`usps-addresses-v3r2_4.yaml`](usps-addresses-v3r2_4.yaml),
`DPVConfirmation`). Both providers were affected; Google's
`uspsData.dpvConfirmation` uses the same codes.

- **Cutover: 2026-10-01 14:59:38 UTC.** The old process stopped serving at
  14:59:35; migration 022 ran at 14:59:37 and the new process served from
  14:59:38. No request was answered in between, so `audit_log` rows before
  14:59:35 carry the old labels and rows from 14:59:38 on carry the new ones.
- **Cache:** migration 022 relabelled every cached `D`/`S` row from its stored
  `dpv_match_code`. Cache hits are correct from the cutover on.
- **`audit_log` was not rewritten.** It records what clients were served, and
  rows older than `AUDIT_RETENTION_DAYS` (default 90) are already in the GCS
  Parquet archive. Before the cutover, `confirmed_missing_secondary` always
  meant DPV `S` and `confirmed_bad_secondary` always meant DPV `D`, so swap the
  two labels to read those rows correctly.
- **Admin dashboard:** the provider view's status breakdown reads `audit_log`,
  so for one retention window after the cutover it mixes both meanings.

## US `confirmed` without a DPV code (GH #262)

Until GH #262, a Google US answer with no CASS DPV code was `confirmed`
whenever its verdict had `addressComplete`; it is now `undetermined`.

- **Cache:** the deploy purges cached rows with `provider = 'google'`,
  `country = 'US'`, `dpv_match_code IS NULL` and `status = 'confirmed'`.
- **`audit_log` was not rewritten.** Rows from before the deploy with
  `provider = 'google'`, a US address, no DPV code and `confirmed` read as
  `undetermined`. The null DPV code identifies them, so no cutover time is
  needed.
- **Admin dashboard:** the provider view counts those rows as confirmed
  until they age out of `audit_log`.
