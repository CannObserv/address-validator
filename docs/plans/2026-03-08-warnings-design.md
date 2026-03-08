# Warnings Design — Issue #1

## Summary

Return a `warnings: list[str]` field on parse and standardize responses whenever
input is silently modified, so callers can verify output is correct.

## Decisions

| Question | Decision |
|---|---|
| Data shape | Upgrade `warning: str \| None` → `warnings: list[str]` on parse; add `warnings: list[str]` to standardize (breaking on parse, additive on standardize) |
| Parse warnings in standardize response | Yes — surface all parse-phase warnings in `StandardizeResponseV1.warnings` when called with a raw address string |
| HTTP status | Always `200` — warnings are informational; non-empty list is the signal |
| Web UI | Out of scope — UI has been removed |

## API contract changes (`models.py`)

- `ParseResponseV1`: remove `warning: str | None`, add `warnings: list[str] = []`
- `StandardizeResponseV1`: add `warnings: list[str] = []`

## Warning messages — parser (`services/parser.py`)

| Case | Message |
|---|---|
| Parenthesized text stripped | `"Parenthesized text removed: '(UPPER LEVEL)'"` (includes the stripped text) |
| Ambiguous parse / dual address merged | `"Ambiguous parse: repeated address numbers joined as range '1804-1810'"` |
| Ambiguous parse (general) | `"Ambiguous parse: repeated labels detected; parse may be inaccurate."` |
| Unit designator recovered from building/landmark field | `"Unit designator recovered from mis-tagged field"` |
| Identifier fragment recovered from city | `"Unit identifier fragment recovered from city field"` |
| Trailing punctuation stripped | **Omitted** — cosmetic normalization, no caller action warranted |

`_parse()` collects warnings into a local `list[str]` and returns it alongside
the response. Internal helpers (`_recover_unit_from_city`,
`_recover_identifier_fragment_from_city`) accept a `warnings` list and append
to it in-place when they modify components.

## Standardizer (`services/standardizer.py`)

`standardize()` gains an `upstream_warnings: list[str] = []` parameter.
The standardize router passes parse warnings through on the raw-address path.
The components-direct path produces `warnings=[]`.

## Test strategy

- Unit tests per warning case in `tests/unit/test_parser.py` — assert exact
  message text
- Unit tests for `warnings` propagation in `tests/unit/test_standardizer.py`
- Existing ambiguous-parse tests updated from `warning` field to `warnings` list
- `warnings == []` asserted on clean inputs (no false positives)

## Out of scope

- Standardizer-phase warnings (unknown suffix substitution, etc.)
- Validate endpoint warnings
