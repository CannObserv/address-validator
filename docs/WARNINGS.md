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
| `Ambiguous parse: repeated address numbers joined as range '{range}'` | `services/parser.py` | Two address numbers were detected and joined into a single range; `{range}` is the joined value. |
| `Ambiguous parse: repeated labels detected; parse may be inaccurate.` | `services/parser.py` | usaddress emitted duplicate component labels; the parse may be unreliable. |
| `Unit designator recovered from mis-tagged field: '{designator}'` | `services/parser.py` | A unit designator was found in a mis-tagged field and reassigned; `{designator}` is the recovered token. |
| `Unit identifier fragment recovered from city field` | `services/parser.py` | A unit identifier fragment was found in the city/locality field and moved to the unit. |
| `Unrecognized unit designator preserved: '{designator}'` | `services/parser.py` | A unit-type token not in `UNIT_MAP` was preserved as-is (GH #129); `{designator}` is the token. |
| `Address has no parseable street line; passing raw input to provider` | `services/validation/pipeline.py` | No street line could be parsed; the raw input is forwarded to the validation provider. |
| `Unrecognized province/territory: '{region}'` | `services/standardizer/ca.py` | A Canadian province/territory value was not recognized and is passed through unchanged; `{region}` is the value. |
| `Provider inferred one or more address components not present in input` | `services/validation/google_provider.py` | The Google provider inferred components absent from the input. |
| `Provider replaced one or more address components` | `services/validation/google_provider.py` | The Google provider replaced one or more input components. |
| `One or more address components are unconfirmed` | `services/validation/google_provider.py` | The Google provider could not confirm one or more components. |
| `Validation provider rejected the address as malformed` | `routers/v2/validate.py` | The validation provider raised a bad-request error (`ProviderBadRequestError`); the address is returned with `validation.status = "error"`. |
