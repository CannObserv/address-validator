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

Each warning also has a machine-readable code (GH #338): a ``WarningCode``
member, paired with its template in ``WARNING_CODES``. Responses expose them as
``warning_codes``, index-aligned with ``warnings``. **Codes are the matching
contract; prose may change.** A code is never renamed, and removing one is a
contract-major change — the frozen list in the drift test enforces both. Prose
becomes a code in one place only: ``core/warning_codes.py``.

This module must contain **only** warning data: string constants, ``CATALOGUE``,
``WarningCode`` and ``WARNING_CODES``. The drift test treats every module-level
upper-case ``str`` as a catalogued warning, so an incidental non-warning string
constant here would be a false positive. Put unrelated constants and any logic
elsewhere.
"""

from enum import StrEnum

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
DELIVERY_LINE_RECOVERED = "Delivery address line recovered from mis-tagged field: '{text}'"
UNIT_LIST_ITEM_RECOVERED = "Unit list item recovered from mis-tagged field: '{item}'"
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
    DELIVERY_LINE_RECOVERED,
    UNIT_LIST_ITEM_RECOVERED,
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


class WarningCode(StrEnum):
    """Machine-readable code for each entry of a response's `warnings`.

    Match on these codes, not on warning prose: prose may be reworded, codes
    are never renamed. `unrecognized` is never emitted by design; if it
    appears, treat it as an unknown warning.
    """

    # Values are written out, never derived from the constant names above, so
    # renaming a constant can't rename a published code.
    PARENTHESIZED_REMOVED = "parenthesized_removed"
    REPEATED_NUMBERS_RANGE = "repeated_numbers_range"
    REPEATED_LABELS = "repeated_labels"
    UNIT_RECOVERED_FROM_FIELD = "unit_recovered_from_field"
    UNIT_FRAGMENT_FROM_CITY = "unit_fragment_from_city"
    LOCALITY_FROM_RECIPIENT = "locality_from_recipient"
    DELIVERY_LINE_RECOVERED = "delivery_line_recovered"
    UNIT_LIST_ITEM_RECOVERED = "unit_list_item_recovered"
    UNRECOGNIZED_UNIT_DESIGNATOR = "unrecognized_unit_designator"
    DUPLICATE_UNIT_COLLAPSED = "duplicate_unit_collapsed"
    NO_PARSEABLE_STREET = "no_parseable_street"
    GENERAL_DELIVERY_DISCARDED = "general_delivery_discarded"
    UNRECOGNIZED_REGION = "unrecognized_region"
    PROVIDER_INFERRED = "provider_inferred"
    PROVIDER_REPLACED = "provider_replaced"
    PROVIDER_UNCONFIRMED = "provider_unconfirmed"
    PROVIDER_REJECTED_MALFORMED = "provider_rejected_malformed"
    PROVIDER_FALLBACK_UNREACHABLE = "provider_fallback_unreachable"
    PROVIDER_SECONDARY_NARROWED = "provider_secondary_narrowed"
    PROVIDER_SECONDARY_OMITTED = "provider_secondary_omitted"
    # Runtime sentinel for a warning no template matches. Keeps
    # ``warning_codes`` index-aligned; tests keep it from ever being emitted.
    UNRECOGNIZED = "unrecognized"


# Template -> code, one entry per CATALOGUE warning. The drift test checks it
# is total over CATALOGUE and uses each code (bar the sentinel) exactly once.
WARNING_CODES: dict[str, WarningCode] = {
    PARENTHESIZED_REMOVED: WarningCode.PARENTHESIZED_REMOVED,
    REPEATED_NUMBERS_RANGE: WarningCode.REPEATED_NUMBERS_RANGE,
    REPEATED_LABELS: WarningCode.REPEATED_LABELS,
    UNIT_RECOVERED_FROM_FIELD: WarningCode.UNIT_RECOVERED_FROM_FIELD,
    UNIT_FRAGMENT_FROM_CITY: WarningCode.UNIT_FRAGMENT_FROM_CITY,
    LOCALITY_FROM_RECIPIENT: WarningCode.LOCALITY_FROM_RECIPIENT,
    DELIVERY_LINE_RECOVERED: WarningCode.DELIVERY_LINE_RECOVERED,
    UNIT_LIST_ITEM_RECOVERED: WarningCode.UNIT_LIST_ITEM_RECOVERED,
    UNRECOGNIZED_UNIT_DESIGNATOR: WarningCode.UNRECOGNIZED_UNIT_DESIGNATOR,
    DUPLICATE_UNIT_COLLAPSED: WarningCode.DUPLICATE_UNIT_COLLAPSED,
    NO_PARSEABLE_STREET: WarningCode.NO_PARSEABLE_STREET,
    GENERAL_DELIVERY_DISCARDED: WarningCode.GENERAL_DELIVERY_DISCARDED,
    UNRECOGNIZED_REGION: WarningCode.UNRECOGNIZED_REGION,
    PROVIDER_INFERRED: WarningCode.PROVIDER_INFERRED,
    PROVIDER_REPLACED: WarningCode.PROVIDER_REPLACED,
    PROVIDER_UNCONFIRMED: WarningCode.PROVIDER_UNCONFIRMED,
    PROVIDER_REJECTED_MALFORMED: WarningCode.PROVIDER_REJECTED_MALFORMED,
    PROVIDER_FALLBACK_UNREACHABLE: WarningCode.PROVIDER_FALLBACK_UNREACHABLE,
    PROVIDER_SECONDARY_NARROWED: WarningCode.PROVIDER_SECONDARY_NARROWED,
    PROVIDER_SECONDARY_OMITTED: WarningCode.PROVIDER_SECONDARY_OMITTED,
}
