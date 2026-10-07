"""Single source of truth for API response-warning strings (GH #131).

Every string appended to a response ``warnings: list[str]`` field is defined
here — nowhere else. Static warnings are plain string constants; parameterised
warnings are ``str.format`` templates with named placeholders, so the wording
is fixed in one place and call sites only supply the variable token.

This is distinct from ``logger.warning(...)`` calls, which are an operational
channel and are intentionally NOT catalogued here.

Keep this module and ``docs/WARNINGS.md`` in sync — the drift test
``tests/unit/test_warnings_catalogue.py`` fails on any divergence. After
adding or changing a warning, update both.

This module must contain **only** warning string constants (plus ``CATALOGUE``):
the drift test treats every module-level upper-case ``str`` as a catalogued
warning, so an incidental non-warning string constant here would be a false
positive. Put unrelated constants elsewhere.
"""

# --- Static warnings (no interpolation) ---

REPEATED_LABELS = "Ambiguous parse: repeated labels detected; parse may be inaccurate."
UNIT_FRAGMENT_FROM_CITY = "Unit identifier fragment recovered from city field"
LOCALITY_FROM_RECIPIENT = "City and state recovered from mis-tagged recipient field"
NO_PARSEABLE_STREET = "Address has no parseable street line; passing raw input to provider"
PROVIDER_INFERRED = "Provider inferred one or more address components not present in input"
PROVIDER_REPLACED = "Provider replaced one or more address components"
PROVIDER_UNCONFIRMED = "One or more address components are unconfirmed"
PROVIDER_REJECTED_MALFORMED = "Validation provider rejected the address as malformed"
PROVIDER_FALLBACK_UNREACHABLE = (
    "Validation incomplete while a fallback provider was unreachable; "
    "a later retry may produce a determination"
)

# --- Parameterised warnings (str.format templates) ---

PARENTHESIZED_REMOVED = "Parenthesized text removed: '{text}'"
REPEATED_NUMBERS_RANGE = "Ambiguous parse: repeated address numbers joined as range '{range}'"
UNIT_RECOVERED_FROM_FIELD = "Unit designator recovered from mis-tagged field: '{designator}'"
UNRECOGNIZED_UNIT_DESIGNATOR = "Unrecognized unit designator preserved: '{designator}'"
DUPLICATE_UNIT_COLLAPSED = "Duplicate secondary unit collapsed into '{designator} {identifier}'"
UNRECOGNIZED_REGION = "Unrecognized province/territory: '{region}'"
GENERAL_DELIVERY_DISCARDED = (
    "PO Box / general delivery omitted from standardized address "
    "because a street address is present: '{text}'"
)
PROVIDER_SECONDARY_NARROWED = (
    "Only unit '{unit}' of address line 2 '{line2}' was sent for validation; "
    "providers check a single USPS Pub 28 unit"
)
PROVIDER_SECONDARY_OMITTED = (
    "Address line 2 '{line2}' was not sent for validation: it has no USPS Pub 28 unit designator"
)

# Authoritative list of every catalogued response warning. The drift test
# checks this against docs/WARNINGS.md in both directions, so a new warning is
# only "live" once it appears here.
CATALOGUE: tuple[str, ...] = (
    PARENTHESIZED_REMOVED,
    REPEATED_NUMBERS_RANGE,
    REPEATED_LABELS,
    UNIT_RECOVERED_FROM_FIELD,
    UNIT_FRAGMENT_FROM_CITY,
    LOCALITY_FROM_RECIPIENT,
    UNRECOGNIZED_UNIT_DESIGNATOR,
    DUPLICATE_UNIT_COLLAPSED,
    NO_PARSEABLE_STREET,
    GENERAL_DELIVERY_DISCARDED,
    UNRECOGNIZED_REGION,
    PROVIDER_INFERRED,
    PROVIDER_REPLACED,
    PROVIDER_UNCONFIRMED,
    PROVIDER_REJECTED_MALFORMED,
    PROVIDER_FALLBACK_UNREACHABLE,
    PROVIDER_SECONDARY_NARROWED,
    PROVIDER_SECONDARY_OMITTED,
)
