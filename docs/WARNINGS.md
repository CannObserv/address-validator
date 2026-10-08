# API Response Warnings

Authoritative, living catalogue of every string that may appear in a response
`warnings: list[str]` field. Consumers parsing `warnings` should treat this
table as the reference for the strings they may receive.

**Source of truth:** all strings are defined in
[`core/warnings.py`](../src/address_validator/core/warnings.py). This document
and that module are kept in sync by the drift test
[`tests/unit/test_warnings_catalogue.py`](../tests/unit/test_warnings_catalogue.py),
which fails CI if either side gains or loses an entry. To add or change a
warning, edit `core/warnings.py` and this table together.

Parameterised entries use `{name}` placeholders (`str.format` fields); the
literal token at runtime is interpolated from the offending input.

This catalogue covers the **response `warnings` channel only**. Operational
`logger.warning(...)` events are a separate channel and are documented in
[`LOGGING.md`](LOGGING.md), not here.

> Supersedes the point-in-time design snapshot
> `docs/plans/2026-03-08-warnings-design.md`.

## Catalogue

| Template | Emitting module | Trigger condition |
|---|---|---|
| `Parenthesized text removed: '{text}'` | `services/parser.py` | Parenthetical content stripped from the input before parsing; `{text}` is the removed inner text. |
| `Ambiguous parse: repeated address numbers joined as range '{range}'` | `services/parse_recovery.py` | Two address numbers were detected and joined into a single range; `{range}` is the joined value. |
| `Ambiguous parse: repeated labels detected; parse may be inaccurate.` | `services/parse_recovery.py` | usaddress emitted duplicate component labels; the parse may be unreliable. |
| `Unit designator recovered from mis-tagged field: '{designator}'` | `services/parse_recovery.py` | A unit designator was found in a mis-tagged field and reassigned; `{designator}` is the recovered token. |
| `Unit identifier fragment recovered from city field` | `services/parse_recovery.py` | A unit identifier fragment was found in the city/locality field and moved to the unit. |
| `City and state recovered from mis-tagged recipient field` | `services/parse_recovery.py` | With no ZIP, usaddress tagged the whole tail after the street as a recipient (`123 MAIN ST BLG A SEATTLE WA` — GH #285); its trailing state and the city before it were moved back to the last line. The city is the last comma segment before the state; an earlier segment that is not a unit (`ATTN JOHN, SEATTLE WA`) stays as the recipient. Any unit at the head of the city is then recovered too. |
| `Delivery address line recovered from mis-tagged field: '{text}'` | `services/parse_recovery.py` | With no street, usaddress tagged a USPS delivery line outside the box fields, so line 1 lost it: a literal `GENERAL DELIVERY` tagged as a landmark or recipient (GH #293), or a military route group tagged as a unit (`PSC 1234 BOX 5678`, `CMR 450 BOX 123`, and `UNIT 2050 BOX 4190` when the state is `AA`/`AE`/`AP` or its spelled-out name — GH #292, #299). The text moves back to the box fields and renders on line 1 (`GENERAL DELIVERY`, `PSC 1234 BOX 5678`). `{text}` is the recovered text. A recipient tail after `GENERAL DELIVERY` that ends in a state is split into city and state with `City and state recovered from mis-tagged recipient field`. |
| `Unit list item recovered from mis-tagged field: '{item}'` | `services/parse_recovery.py` | A unit identifier list joined with `&` or `AND` (`STE 100 & 101`, `STE 100 AND 101`, `UNIT 1 AND 2`) had its second identifier tagged as an intersection street, a USPS box or a second unit (GH #288, #297), which moved it to line 1 or dropped it. It is folded back into the unit before it as `<id> & <item>` (line 2 `STE 100 & 101`). `{item}` is the recovered identifier. Providers are sent the first identifier only, with `Only unit '{unit}' of address line 2 '{line2}' was sent for validation; …`. |
| `Unrecognized unit designator preserved: '{designator}'` | `services/parse_recovery.py` | A unit-type token not in `UNIT_MAP` was preserved as-is (GH #129); `{designator}` is the token. A list joiner tagged as a unit type (`AND` in `UNIT 1 AND 2`) is not warned about: it is folded back into the unit with `Unit list item recovered from mis-tagged field: '{item}'` (GH #297). |
| `Duplicate secondary unit collapsed into '{designator} {identifier}'` | `services/parse_recovery.py` | A bare `#` unit phrase (or a `#` alias: `NO`/`NUM`/`NUMBER` — GH #286) restated the same unit as a named designator, before or after it (`#1, UNIT 1` — GH #170; `NO 1 STE 1` — GH #286; `STE 1 #1`, `APT 1 NO 1` — GH #290); the `#` phrase was dropped and the named unit kept. |
| `Address has no parseable street line; passing raw input to provider` | `services/validation/pipeline.py` | No street line could be parsed; the raw input is forwarded to the validation provider. |
| `Only unit '{unit}' of address line 2 '{line2}' was sent for validation; providers check a single USPS Pub 28 unit` | `services/validation/pipeline.py` | US `/validate` with a provider configured, where line 2 holds more than one unit (`SMP 2 STE J`, `BLDG 1 STE 100`, `UNIT 3 STE 4`, `# 5 # 6` — GH #287), or a range or list of identifiers (`STE 100-102`, `STE 100, 101` — GH #289; `STE 100 & 101`, `STE 100 AND 101` — GH #297). The provider gets one unit: a non-Pub-28 designator (`SMP`, kept by #129) is dropped, a specific unit beats a container (`BLDG`/`FL`), among equals the first unit on line 2 wins, and a range or list sends its first identifier. `{unit}` is the unit sent; `{line2}` the full standardized line 2. The response's address fields are the provider's, so the rest of line 2 is not in them. |
| `Address line 2 '{line2}' was not sent for validation: it has no USPS Pub 28 unit designator` | `services/validation/pipeline.py` | As above, but no unit on line 2 has a Pub 28 designator (`SMP 2` alone — GH #287), so the provider gets no secondary. |
| `PO Box / general delivery omitted from standardized address because a street address is present: '{text}'` | `services/standardizer/us.py` | The input has a street line and a PO Box / general-delivery component (`PO BOX 5`, `LOCKER 7` — GH #285). Line 1 renders the street; the box stays in `components` but is left off the address lines. `{text}` is the omitted box. When parsing, a box whose type is a unit designator (`SUITES 100`) is moved onto line 2 instead and reported as a recovered unit, unless both unit slots are already full; components sent directly skip that recovery, so such a box gets this warning. |
| `Unrecognized province/territory: '{region}'` | `services/standardizer/ca.py` | A Canadian province/territory value was not recognized and is passed through unchanged; `{region}` is the value. |
| `Provider inferred one or more address components not present in input` | `services/validation/google_provider.py` | The Google provider inferred components absent from the input. |
| `Provider replaced one or more address components` | `services/validation/google_provider.py` | The Google provider replaced one or more input components. |
| `One or more address components are unconfirmed` | `services/validation/google_provider.py` | The Google provider could not confirm one or more components. |
| `Validation incomplete while a fallback provider was unreachable; a later retry may produce a determination` | `services/validation/chain_provider.py` | No provider determined the address — each one that answered returned `validation.status = "undetermined"` or, for US input, a verdict with no DPV code (`invalid`/`not_found`, GH #275) — and at least one provider in the chain failed transiently (429 / at capacity / 5xx / unreachable — GH #257). Rides on `undetermined`, `invalid` or `not_found`. The result is not cached (GH #250). Clients matching it should key on the stable suffix `a later retry may produce a determination` (worded `Validation undetermined while …` before GH #275). |
| `Validation provider rejected the address as malformed` | `routers/v2/validate.py` | The validation provider raised a bad-request error (`ProviderBadRequestError`); the address is returned with `validation.status = "error"`. |
