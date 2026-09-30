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
which fails CI if any side gains or loses an entry. To add or change a status,
edit `core/validation_status.py` and this table together.

This mirrors the response-warning catalogue pattern
([`WARNINGS.md`](WARNINGS.md), GH #131/#132).

## Catalogue

| Status | DPV code | Meaning |
|---|---|---|
| `confirmed` | Y | Fully confirmed delivery point. |
| `confirmed_missing_secondary` | S | Building confirmed, unit (secondary) missing. |
| `confirmed_bad_secondary` | D | Building confirmed, unit (secondary) unrecognised. |
| `not_confirmed` | N | Address not found in the USPS database. |
| `not_found` | — | Google verdict (non-US, or US without a CASS DPV code): address could not be geocoded or verified. |
| `invalid` | — | Google verdict (non-US, or US without a CASS DPV code): address is geocodable but incomplete. |
| `undetermined` | — | A provider answered (HTTP 200) but made no determination — e.g. USPS returned no DPV code. An answer about the address, not an outage: retrying returns the same answer. When a fallback provider was unreachable, the response carries a warning and a retry may yield a determination (GH #250). |
| `unavailable` | — | No validation provider is configured. Never a per-address outcome; an outage surfaces as HTTP 429/5xx, not as a status. |
| `error` | — | Provider rejected the input as malformed. |
