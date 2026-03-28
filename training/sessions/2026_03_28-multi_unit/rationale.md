# Labeling Rationale — multi-unit

## Problem

Addresses with two secondary-unit designators (e.g., `BLDG 201 ROOM 104`) cause
`usaddress.RepeatedLabelError`. The CRF model does not distinguish between
building-level and unit-level designators, assigning both the same label and
triggering the repeated-label exception. See issue #72.

## Governing standards

### FGDC US Address Data Standard

The usaddress label set originates from the FGDC "United States Thoroughfare,
Landmark, and Postal Address Data Standard" (referenced in usaddress source code).
The FGDC standard defines a structural hierarchy:

- **SubaddressType / SubaddressIdentifier** — the outer, larger container
  (building, tower, wing, department, mail code)
- **OccupancyType / OccupancyIdentifier** — the inner, smaller unit of occupancy
  (suite, apartment, room, unit)

### USPS Publication 28

Pub 28 (Appendix C2, Section 213) lists 25 approved secondary unit designators
but does **not** categorize them into semantic tiers. The ZIP+4 file format has
a single 4-character field for one designator — there is no provision for two.
Pub 28 is silent on multiple secondary units per address line.

### Upstream training data (`datamade/usaddress` labeled.xml)

Empirical analysis of 29 addresses in upstream training data containing **both**
`SubaddressType` and `OccupancyType` confirms the hierarchy:

| Designator | Label (standalone) | Label (paired) | Evidence |
|---|---|---|---|
| BLDG | `SubaddressType` | `SubaddressType` (outer) | 12/12 instances |
| DEPT | `SubaddressType` | `SubaddressType` (outer) | 24/24 instances |
| APT | `OccupancyType` | `OccupancyType` (inner) | 29/29 instances |
| STE/SUITE | `OccupancyType` | `OccupancyType` (inner) | 164/164 instances |
| RM/ROOM | `OccupancyType` | `OccupancyType` (inner) | 13/14 instances |
| UNIT | `OccupancyType` | `OccupancyType` (inner) | 23/24 instances |
| FL/FLOOR | `OccupancyType` (77%) | ambiguous | 35/43 Occupancy, 8/43 Subaddress |

Representative upstream examples:
- `1640 Powers Ferry Road Building 1, Suite 100` → BLDG=Subaddress, STE=Occupancy
- `4343 Shallowford Rd Bldg B, Suite 8A` → BLDG=Subaddress, STE=Occupancy
- `1800 M Street NW North Tower, Ste. 700` → Tower=Subaddress, STE=Occupancy

## Scope of this training data

### Included: BLDG-first patterns only

This training data covers addresses where BLDG is the outer designator paired
with an inner unit designator. These patterns are well-supported because BLDG is
**already exclusively** `SubaddressType` in upstream data (12/12), so the CRF
only needs to learn the transition from `SubaddressIdentifier` → `OccupancyType`
for the second designator.

| Pattern | Training count | Basis |
|---|---|---|
| BLDG + ROOM | 21 | Canonical: BLDG=SubaddressType (12/12 upstream), ROOM=OccupancyType (8/8 upstream) |
| BLDG + STE | 15 | Canonical upstream pattern (e.g., `Bldg B, Suite 8A`) |
| BLDG + APT | 15 | Follows same hierarchy: APT=OccupancyType (29/29 upstream) |
| BLDG + UNIT | 10 | Follows same hierarchy: UNIT=OccupancyType (23/24 upstream) |

### Excluded: non-BLDG outer designator patterns

The following patterns were **attempted but removed** because the CRF model
cannot learn them without conflicting with the upstream training distribution:

| Pattern | Why excluded |
|---|---|
| BLDG + RM | Despite RM being `OccupancyType` in 5/6 upstream instances, the CRF consistently labels RM as `SubaddressType` after a `SubaddressIdentifier`. The two-character token "RM" may share CRF features with other short abbreviations that are `SubaddressType`. 15 training examples were insufficient to overcome this. BLDG + ROOM (the unabbreviated form) works correctly. |
| STE + RM | STE is `OccupancyType` in 106/108 upstream instances. Teaching the CRF that STE can be `SubaddressType` (when outer) requires overcoming this strong prior. 20 custom examples were insufficient; all test cases failed. |
| SUITE + RM | Same issue: SUITE is `OccupancyType` in 58/58 upstream instances. |
| SUITE + ROOM | Same issue. |
| APT + SUITE | Both are exclusively `OccupancyType` in upstream. No upstream precedent for either as `SubaddressType`. |

**Root cause analysis:** The usaddress CRF feature set includes previous/next
token properties but not previous/next *labels*. Label transitions are modeled
separately by the CRF, but the feature overlap between "STE as the only
designator" and "STE as the first of two designators" is too high for the
transition weights to overcome 106 upstream examples. A feature-set change
(e.g., adding a "previous token is a known unit designator" feature) would be
needed to make these patterns learnable.

These patterns are handled by the parser's existing recovery heuristics in
`parser.py` (`_collect_ambiguous_components`, `_next_free_unit_slot`).

### Known edge case: long numeric BLDG identifiers

BLDG + ROOM fails when the BLDG identifier is a 3-digit number (e.g.,
`BLDG 201 ROOM 104`). The CRF confuses the numeric BLDG identifier with an
`AddressNumber`, breaking the label transition chain. BLDG identifiers that are
letters or 1-2 digit numbers parse correctly. The training data uses letter and
short-number identifiers to avoid teaching the model an unreliable pattern.

The #72 issue address (`995 9TH ST BLDG 201 ROOM 104 T`) falls into this edge
case and is still handled by the recovery heuristics.

## Designators excluded from training data entirely

| Designator | Reason |
|---|---|
| OFFICE | Zero instances as OccupancyType/SubaddressType in upstream (labeled USPSBoxType/Recipient/BuildingName) |
| SPACE/SPC | Near-zero upstream data (1 instance as SubaddressType) — insufficient basis |
| FLOOR/FL | Ambiguous in upstream (77% Occupancy, 23% Subaddress) — deferred to future training run with more data |
