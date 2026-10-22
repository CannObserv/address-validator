# Machine-readable `warning_codes` — Design (GH #338)

**Date:** 2026-10-21
**Issue:** #338 (split from #276 piece b)
**Refs:** #276, #275, #262

## Problem

`warnings: list[str]` is prose only. Consumers (WordPress) match sentences
word-for-word. #275 reworded `PROVIDER_FALLBACK_UNREACHABLE`; prod has been
multi-provider since 10-09, so that warning can now fire and the consumer's
matcher is likely broken.

## Approach

### 1. `WarningCode` enum in `core/warnings.py`

A `StrEnum` whose members mirror the existing constant names, lowercased:

```python
class WarningCode(StrEnum):
    parenthesized_removed = "parenthesized_removed"
    repeated_numbers_range = "repeated_numbers_range"
    repeated_labels = "repeated_labels"
    unit_recovered_from_field = "unit_recovered_from_field"
    unit_fragment_from_city = "unit_fragment_from_city"
    locality_from_recipient = "locality_from_recipient"
    delivery_line_recovered = "delivery_line_recovered"
    unit_list_item_recovered = "unit_list_item_recovered"
    unrecognized_unit_designator = "unrecognized_unit_designator"
    duplicate_unit_collapsed = "duplicate_unit_collapsed"
    no_parseable_street = "no_parseable_street"
    general_delivery_discarded = "general_delivery_discarded"
    unrecognized_region = "unrecognized_region"
    provider_inferred = "provider_inferred"
    provider_replaced = "provider_replaced"
    provider_unconfirmed = "provider_unconfirmed"
    provider_rejected_malformed = "provider_rejected_malformed"
    provider_fallback_unreachable = "provider_fallback_unreachable"
    provider_secondary_narrowed = "provider_secondary_narrowed"
    provider_secondary_omitted = "provider_secondary_omitted"
```

A `WARNING_CODE_MAP: dict[str, WarningCode]` maps each constant name to its
enum value, and a `TEMPLATE_TO_CODE` reverse map is built from the templates.
Codes are never renamed; removing one is a contract-major event.

### 2. Derive codes from prose — single derivation point

A function `resolve_warning_codes(warnings: list[str]) -> list[WarningCode]`
lives in `core/warnings.py`. It builds regex patterns from the templates
(each `{placeholder}` → `.*?`, anchored `^…$`) and matches the final prose
strings. This runs once, wherever we serialize a response — both fresh answers
and cache hits go through it.

**Unmatched strings:** Should not happen (drift test keeps sync). If one does
at runtime, emit `unrecognized` sentinel to preserve index alignment, log a
WARNING with **count only** (never the text — templated warnings carry address
fragments that are PII at WARNING level per logging policy).

The sentinel means adding one extra enum member:
```python
    unrecognized = "unrecognized"  # runtime fallback, never emitted by design
```

### 3. Response models — `computed_field`

All 3 models (`ParseResponseV2`, `StandardizeResponseV2`, `ValidateResponseV2`)
get a Pydantic `@computed_field` that derives `warning_codes` from `warnings`:

```python
@computed_field
@property
def warning_codes(self) -> list[WarningCode]:
    return resolve_warning_codes(self.warnings)
```

`computed_field` serializes into JSON and appears in FastAPI's OpenAPI schema.

### 4. Index-aligned

`len(warning_codes) == len(warnings)`, same order. `warning_codes[i]` is the
code of `warnings[i]`.

### 5. Cache parity — no migration, no PIPELINE_CODE_VERSION bump

Cache stores prose in `warnings_json`. `_row_to_response` builds a
`ValidateResponseV2` with `warnings=<prose list>`. The computed field derives
codes at serialization time. Fresh and cached paths converge at the same model;
codes are always consistent. No schema migration, no version bump, no quota
spend.

### 6. Reverse-map construction

The templates contain `str.format` placeholders (`{text}`, `{range}`, etc.).
For each template:
1. Escape the literal portions for regex
2. Replace each `{name}` with `.*?` (non-greedy)
3. Anchor with `^` and `$`
4. Compile once at module load

Matching order: **static (no placeholders) templates first** — they're exact
string equality checks and can't collide. Then parameterised templates by
decreasing length (longer patterns are more specific).

### 7. Adversarial-interpolation safety

A formatted warning could contain text that matches another template. The
resolution function tests the **known constant name → template** map first via
exact `str ==` for static warnings, then applies regex patterns. Because each
formatted string includes its own fixed prefix/suffix from the template, and
patterns are anchored, cross-matching requires the interpolated text to
reproduce the entire surrounding sentence — which can't happen with real
address fragments.

The test suite includes an adversarial case to prove this.

## Tradeoffs

| Choice | Pro | Con |
|---|---|---|
| Derive at serialization vs. store in DB | No migration, no pipeline bump, cache parity for free | ~20 regex matches per response (negligible) |
| `computed_field` vs. explicit field | Auto-derives, single source of truth, no emission-site changes | Read-only in the model (fine — codes are derived) |
| `unrecognized` sentinel vs. `None` | Keeps `list[WarningCode]` type clean, preserves alignment | One extra enum member that should never appear |

## Steps

1. Add `WarningCode` enum, `WARNING_CODE_MAP`, reverse-map builder, and
   `resolve_warning_codes()` to `core/warnings.py`
2. Add `@computed_field warning_codes` to the 3 response models in `models.py`
3. Extend `tests/unit/test_warnings_catalogue.py`: every constant has a code,
   every code has a constant, `docs/WARNINGS.md` lists each code
4. New test: reverse-map round-trip — each template's sample formats map back
   to their own code; adversarial interpolation case
5. New test: cache parity — a `ValidateResponseV2` built from a stored row has
   the same `warning_codes` as a fresh one
6. New test: schema — `warning_codes` appears in OpenAPI with enum values
7. Update `docs/WARNINGS.md`: add code column, add the rule that codes are the
   matching contract
8. Regenerate `openapi.json` via `uv run python scripts/export_openapi.py`
9. CHANGELOG entry

## Open questions

- **WordPress matcher:** Is the current sentence match broken since #275?
  Operator to confirm. Adoption note on that repo to switch to
  `provider_fallback_unreachable` code.

## Out of scope

- `PIPELINE_CODE_VERSION` bump (not needed — codes are derived, not stored)
- DB migration (no schema change)
- Removing `warnings` field (breaking; future versioning concern, #276)
