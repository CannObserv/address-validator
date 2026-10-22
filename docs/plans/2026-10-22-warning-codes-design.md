# Machine-readable `warning_codes` — Design (GH #338)

**Date:** 2026-10-22 (rev 2; rev 1 of 2026-10-21 superseded — see *Revision notes*)
**Issue:** #338 (split from #276 piece b) · **Refs:** #276, #275, #262, #339
**Checked against:** `origin/main` `5192c2b`

## Problem

`warnings: list[str]` is prose only. Consumers branch on it by matching
sentences word for word. CannObserv/cannabis.observer-wordpress matches
`PROVIDER_FALLBACK_UNREACHABLE` for its "try again later" path; #275
(2026-10-05) reworded that sentence, and prod has run `usps,google` since
2026-10-09, so the warning can fire and the matcher is likely broken.

## Decisions

| Question (from #338) | Decision |
|---|---|
| Alignment | Index-aligned: `len(warning_codes) == len(warnings)`, `warning_codes[i]` is the code of `warnings[i]` |
| Templated warnings | One code per template; parameters never appear in the code |
| Cached rows | Derive codes from prose at serialization. No migration, no `PIPELINE_CODE_VERSION` bump |
| Naming | Explicit string literals, frozen by a test. Never renamed; removal is contract-major |

## Design

### 1. Codes — `core/warnings.py` (data only)

```python
class WarningCode(StrEnum):
    PARENTHESIZED_REMOVED = "parenthesized_removed"
    ...                                   # one per catalogued warning (20)
    PROVIDER_FALLBACK_UNREACHABLE = "provider_fallback_unreachable"
    UNRECOGNIZED = "unrecognized"         # runtime sentinel, see §3

WARNING_CODES: dict[str, WarningCode] = {
    PARENTHESIZED_REMOVED: WarningCode.PARENTHESIZED_REMOVED,
    ...
}
```

- Code values are **written-out literals**, not derived from the Python constant
  name, so renaming a constant can't rename a code.
- `WARNING_CODES` pairs each template with its code explicitly; it replaces
  nothing — `CATALOGUE` stays.
- The module keeps its "data only" contract (its docstring and the drift test
  treat every module-level upper-case `str` as a warning; the enum class and a
  dict are not `str`, so they don't trip it). Docstring updated to admit them.

### 2. Resolver — new `core/warning_codes.py`

`resolve_warning_codes(warnings: Sequence[str]) -> list[WarningCode]`, the
**only** place prose becomes a code. Kept out of `core/warnings.py` so that
module stays data.

Built once at import:

- **Static warnings:** a `dict[str, WarningCode]` — exact lookup.
- **Templated warnings:** tokenised with `string.Formatter().parse` (handles
  `{{`/`}}` escapes correctly, unlike an ad-hoc `\{\w+\}` regex), literals
  `re.escape`d, each field → `.*`, compiled with **`re.DOTALL`** and applied
  with **`fullmatch`**.

  rev 1 proposed `^…$` with `.*?` and no `DOTALL`. That fails on real input:
  `456 Oak Ln (rear\nentrance) Boise ID 83702` emits
  `Parenthesized text removed: 'rear\nentrance'` (parser strips only the ends
  of the parenthetical), which the rev 1 pattern doesn't match — verified.
  `$` also matches before a trailing `\n`; `fullmatch` doesn't.

- **No ordering heuristic.** Unambiguity is a provable invariant instead: take
  each warning's *fixed head* (a static's full text; a template's literal text
  before its first field). If **no head is a prefix of any other head** (any
  kind, both directions — a static that prefixes a template head could collide
  when the field is empty), then any string matches **at most one** entry
  whatever the interpolated text: two anchored patterns with diverging heads
  can't both match. Holds today (checked: 0 collisions across 20). A
  test enforces it, so a future template that breaks it fails CI rather than
  mis-coding silently. rev 1's "longest first" ordering is dropped.

### 3. Unmatched strings

Shouldn't happen (§6 tests). At runtime, the resolver emits
`WarningCode.UNRECOGNIZED` in that slot to keep alignment and logs **one
WARNING per call with the count only**: `warning_codes: %d unrecognized
warning(s)`. **Never the text** — templated warnings carry address fragments,
and WARNING lines leave the host in unit-failure tails (#252). New row in
`docs/LOGGING.md`'s event table (rev 1 missed this).

`unrecognized` is a public enum value: documented as "never emitted by design;
treat as an unknown warning". It's exempt from the "every code has a warning"
drift check.

### 4. Response models — `computed_field`

On `ParseResponseV2`, `StandardizeResponseV2`, `ValidateResponseV2`:

```python
@computed_field(description="Machine-readable code for each entry of `warnings`, "
                "same length and order. Match on codes; warning prose may change.")
@property
def warning_codes(self) -> list[WarningCode]:
    return resolve_warning_codes(self.warnings)
```

Probed with fastapi 0.143.3 / pydantic 2.12.5 on a scratch app:

- Schema: `warning_codes` is `readOnly: true` and in `required`; items are
  `$ref: #/components/schemas/WarningCode`, an `enum` of all values. The 3
  models are response-only, so no `-Input`/`-Output` component split —
  component names in `openapi.json` don't change.
- `model_copy(update={"warnings": …})` recomputes codes. This matters: warnings
  are amended by `model_copy` at `routers/v2/validate.py:192`,
  `chain_provider.py:187` and `pipeline.py:125/135`. A *stored* field would
  go stale at each of those; a computed one can't.
- `Model(**m.model_dump())` round-trips (the extra key is ignored; no model
  sets `extra="forbid"`).
- `StandardizedAddress` is an alias of `StandardizeResponseV2`, so internal
  service objects gain the property too. Harmless: computed only on access or
  dump.

### 5. Cache parity — no migration, no bump

A cache hit builds `ValidateResponseV2(warnings=row["warnings_json"])`, so it
goes through the same computed field as a fresh answer.

Stale-prose risk, checked in code rather than assumed:

- `warnings_json` holds the **provider** result's warnings only. Parse/
  standardize warnings are prepended per request (`validate.py:192`), never
  read from the row.
- Providers emit only `PROVIDER_INFERRED` / `_REPLACED` / `_UNCONFIRMED`
  (Google) and `PROVIDER_FALLBACK_UNREACHABLE` (chain).
- Of all 20, the **only** warning ever reworded is
  `PROVIDER_FALLBACK_UNREACHABLE` (#275; from `git log` over
  `core/warnings.py`), and a result carrying it is **never cached**
  (`cache_provider.py` ~L454, #250).

So no row in prod can hold prose the resolver won't match. The sentinel is
defence for the future. Not checked: prod `warnings_json` itself (would need
the prod DSN; count-only query available if wanted).

Output pin unaffected: `test_pipeline_output_pin.py` hashes an explicit dict
(L121 `"warnings": std.warnings`), not `model_dump()`.

### 6. Tests

`tests/unit/test_warnings_catalogue.py` (extend):
- every `CATALOGUE` entry has a `WARNING_CODES` entry and vice versa; every
  `WarningCode` except `UNRECOGNIZED` is used exactly once
- `docs/WARNINGS.md`: each row's template **and its code on the same row**
  (pairing, not just presence)
- **frozen code list:** a literal set of every shipped code value; the enum
  must be a superset. Renaming or removing a code fails here.

`tests/unit/test_warning_codes.py` (new):
- each template formatted with sample values (incl. empty, `'`, `{`, `\n`,
  and another template's full text) resolves to its own code
- head-disjointness invariant (§2)
- unmatched string → `UNRECOGNIZED`, alignment kept, one WARNING logged whose
  message and args contain the count and **not** the text (`caplog`)
- each of the 3 models: `warning_codes` in `model_dump()` and JSON, aligned
- cache parity: `_row_to_response` on a row with Google warnings gives the same
  `warning_codes` as the fresh `ValidateResponseV2`
- OpenAPI: the 3 response schemas reference `WarningCode`; its `enum` equals
  the enum's values

Integration: one `/api/v2/parse` assertion through `TestClient` with the
parenthetical input.

### 7. Contract artifacts

- `openapi.json` regenerated (`uv run python scripts/export_openapi.py`), same PR
- `info.version` stays `3.0.0`: the change is additive and no repo policy yet
  ties additive changes to a version bump (#276's versioning is open)
- `docs/WARNINGS.md`: `Code` column; rule that **codes are the matching
  contract and prose may change**; sentinel described
- `docs/LOGGING.md`: unrecognized-warning event row
- `CHANGELOG.md` *Added*, with a **Client-facing** note naming
  `provider_fallback_unreachable`

## Tradeoffs

| Choice | For | Against |
|---|---|---|
| Derive at serialization vs. store codes | No migration, no bump, no quota; immune to `model_copy` staleness | Regex per warning per dump (≤20 patterns, tiny lists — negligible) |
| `computed_field` vs. setting at ~10 emission sites | One derivation point; emission sites untouched | Prose stays the internal carrier, so the resolver must stay total (tests do that) |
| `UNRECOGNIZED` sentinel vs. dropping the slot | Keeps alignment and `list[WarningCode]` | One public enum value that should never appear |
| Separate `core/warning_codes.py` | `core/warnings.py` stays data-only | One more module |

## Steps

1. `WarningCode` + `WARNING_CODES` in `core/warnings.py`; docstring
2. `core/warning_codes.py` resolver + logging
3. `computed_field` on the 3 models
4. Tests (§6), TDD order
5. Docs (§7), regenerate `openapi.json`, CHANGELOG
6. `CR --fix`; pause before restart; restart; live check on
   `/api/v2/parse` with `456 Oak Ln (rear entrance) Boise ID 83702`; pause
   before shipping

## Open questions (operator)

- Is the WordPress matcher broken since #275 + `usps,google` (10-09)? Sets the
  urgency of the adoption note there, which is the operator's to file.

## For #339 (PHP SDK)

`warning_codes` is `required` + `readOnly`, items `$ref WarningCode`. Generate
the enum from the schema, but tolerate unknown values (new codes are additive)
and `unrecognized`.

## Out of scope

- `PIPELINE_CODE_VERSION` bump; DB migration
- Removing or deprecating `warnings` prose (contract-major; #276)
- Versioning policy for `info.version`

## Revision notes (rev 1 → rev 2)

1. **Bug:** regex `^…$` / `.*?` without `DOTALL` → `fullmatch` + `DOTALL`;
   newline-bearing parentheticals are real input
2. Ordering heuristic → provable head-disjointness invariant + test
3. Codes derived from constant names → explicit literals + frozen-list test
4. Resolver moved out of data-only `core/warnings.py`
5. Cache staleness analysed from code (rev 1 asserted parity without checking
   stored prose)
6. `computed_field` schema/`model_copy` behaviour probed, not assumed
7. Added: `LOGGING.md` row, doc row-pairing check, `info.version` decision,
   `For #339` note
