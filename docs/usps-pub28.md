# USPS Publication 28 — Reference Notes

This document records what is known about the edition of USPS Publication 28
that the `usps_data/` tables are based on, how that determination was made,
and what related USPS materials are archived here.

---

## Spec version used by this project

The `usps_data/` lookup tables (suffixes, directionals, states, unit
designators) were generated from LLM training-data knowledge of USPS
Publication 28 during the initial service implementation on 2026-02-20.
No specific edition of the publication was consulted or cited at the time.

### Edition research (conducted 2026-03-03)

Attempts to determine the exact edition:

1. **USPS Postal Explorer** (`pe.usps.com/text/pub28/`) — HTML pages were
   accessible but did not surface a dated edition number in the page content
   retrieved.  The appendix page (`pub28apb.htm`) mentioned "January 2026" in
   maintenance-alert banner text, which appears to be a site-wide alert rather
   than a publication edition date.

2. **USPS Publication 28 PDF** (`pe.usps.com/cpim/ftp/pubs/Pub28/pub28.pdf`)
   — redirected to a maintenance page; direct download was unavailable at time
   of research.

3. **Cross-reference against USPS Addresses API v3** — The USPS Addresses API
   v3.2.2 OpenAPI spec (see `usps-addresses-v3r2_3.yaml`) links to
   `pe.usps.com/text/pub28/28c2_003.htm` for secondary unit designator
   definitions, confirming Pub 28 is the normative reference for the API.  The
   spec's `version: 3.2.2` refers to the API revision, not the Pub 28 edition.

### Current `spec_version` value

`usps_data/spec.py` sets `USPS_PUB28_SPEC_VERSION = "2024-10"`.

**Resolved 2026-04-09** — `pe.usps.com/text/pub28/welcome.htm` was accessible
and returned the edition string: "Publication 28 - Postal Addressing Standards
October 2024 PSN 7610-03-000-3688".  This is the current edition as of that
date.  `spec.py` has been updated accordingly.

---

## USPS Addresses API v3 — model notes

Archived: `docs/usps-addresses-v3r2_3.yaml` (OpenAPI 3.0.1, API version 3.2.2)
Source: `https://developers.usps.com/sites/default/files/apidoc_specs/addresses-v3r2_3.yaml`
Retrieved: 2026-03-03

### Key fields in the USPS API response (`DomesticAddress` / `Address`)

The USPS API uses these field names for its standardized address output:

| USPS API field | Our field | Notes |
|---|---|---|
| `streetAddress` | `address_line_1` | Primary street line |
| `streetAddressAbbreviation` | — | Abbreviated form; read-only |
| `secondaryAddress` | `address_line_2` | Unit/suite/apt |
| `city` | `city` | |
| `cityAbbreviation` | — | Read-only |
| `state` | `region` | 2-char code |
| `ZIPCode` | `postal_code` (5-digit part) | Pattern: `\d{5}` |
| `ZIPPlus4` | `postal_code` (plus-4 part) | Pattern: `\d{4}` |
| `urbanization` | — | Puerto Rico only |
| `firm` | — | Business name; not currently modelled |

The USPS API also returns:
- `additionalInfo` — `deliveryPoint`, `carrierRoute`, `DPVConfirmation`,
  `DPVCMRA`, `business`, `centralDeliveryPoint`, `vacant`
- `corrections` — array of correction codes (e.g., `32` = needs apt number)
- `matches` — array of match codes (e.g., `31` = exact match)
- `warnings` — array of warning strings

### Observations relevant to our models

- The USPS API splits ZIP into `ZIPCode` (5-digit) and `ZIPPlus4` (4-digit)
  separately, whereas we combine them as `XXXXX-XXXX` in `postal_code`.
  Splitting may be worth considering if we ever integrate with the USPS API
  directly.

- `DPVConfirmation` (`Y/D/S/N`) and the correction/match arrays would be
  natural additions to our `StandardizeResponseV1` if we route through the
  USPS API for verification in a future version.

- The `firm` field (business name, max 50 chars) maps to usaddress's
  `Recipient` tag, which we currently expose as `recipient` in the components
  dict but do not surface as a top-level response field.

- `urbanization` (Puerto Rico) is not currently modelled; it would be needed
  for full PR address support.

---

## Pub 28 sections referenced in this codebase

| Section | Topic | Referenced in |
|---|---|---|
| §232 | Dual/range addresses | `services/parser.py` |
| §354 | Parentheses not valid in standardized addresses | `services/parser.py`, `services/standardizer.py` |
| Appendix B | Street suffix abbreviations | `usps_data/suffixes.py` |
| Appendix C | Secondary unit designators | `usps_data/units.py` |
| Appendix D | State abbreviations | `usps_data/states.py` |
| Appendix E/F | Directional abbreviations | `usps_data/directionals.py` |
| Appendix H | Designators that never require an identifier | `services/parser.py` (`_NO_ID_DESIGNATORS`) |
