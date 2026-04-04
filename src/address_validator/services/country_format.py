"""Per-country address field format service.

Maps ``google-i18n-address`` (i18naddress) ``ValidationRules`` to
:class:`~models.CountryFormatResponse`.  Used by the
``GET /api/v1/countries/{code}/format`` route.
"""

from i18naddress import ValidationRules, get_validation_rules

from address_validator.models import (
    CountryFieldDefinition,
    CountryFormatResponse,
    CountrySubdivision,
)

# Format string token → i18naddress field key.
# Only the four tokens that map to our response field keys are included.
# %D (dependent locality / sub-district) is intentionally excluded — it
# does not have a corresponding key in our address model.
_FORMAT_TOKENS: dict[str, str] = {
    "%A": "street_address",
    "%C": "city",
    "%S": "country_area",
    "%Z": "postal_code",
}

# i18naddress country_area_type → display label
# "canton", "oblys", and "region" are forward-compat entries — they are not
# present in any country's data in the current i18naddress release but are
# documented in the upstream spec as valid values.
_AREA_TYPE_LABELS: dict[str, str] = {
    "area": "Area",
    "canton": "Canton",
    "county": "County",
    "department": "Department",
    "district": "District",
    "do_si": "Province/City",
    "emirate": "Emirate",
    "island": "Island",
    "oblast": "Region",
    "oblys": "Region",
    "parish": "Parish",
    "prefecture": "Prefecture",
    "province": "Province",
    "region": "Region",
    "state": "State",
}

# i18naddress city_type → display label
_CITY_TYPE_LABELS: dict[str, str] = {
    "city": "City",
    "district": "District",
    "post_town": "Town/City",
    "suburb": "Suburb",
}

# i18naddress postal_code_type → display label
_POSTAL_TYPE_LABELS: dict[str, str] = {
    "eircode": "Eircode",
    "pin": "PIN code",
    "postal": "Postal code",
    "zip": "ZIP code",
}


def get_country_format(country_code: str) -> CountryFormatResponse | None:
    """Return address field format for *country_code*, or ``None`` if unavailable.

    Returns ``None`` when the ``google-i18n-address`` library raises
    ``ValueError`` for the given code (unknown country).  The caller is
    responsible for translating ``None`` to a 404 response.
    """
    try:
        rules = get_validation_rules({"country_code": country_code})
    except ValueError:
        return None

    fields: list[CountryFieldDefinition] = []
    for lib_key in _parse_format_order(rules.address_format):
        field = _build_field(lib_key, rules)
        if field is None:
            continue
        fields.append(field)
        if lib_key == "street_address":
            fields.append(
                CountryFieldDefinition(
                    key="address_line_2",
                    label="Address line 2",
                    required=False,
                )
            )

    return CountryFormatResponse(country=country_code, fields=fields)


def _parse_format_order(address_format: str) -> list[str]:
    """Return lib field keys in the order they appear in *address_format*.

    Uses ``str.index()`` which returns the first occurrence; i18naddress
    format strings do not contain duplicate tokens in practice.
    """
    positions: list[tuple[int, str]] = []
    for token, lib_key in _FORMAT_TOKENS.items():
        if token in address_format:
            positions.append((address_format.index(token), lib_key))
    positions.sort()
    return [lib_key for _, lib_key in positions]


def _build_field(lib_key: str, rules: ValidationRules) -> CountryFieldDefinition | None:
    """Return a :class:`CountryFieldDefinition` for *lib_key*, or ``None``."""
    required = lib_key in rules.required_fields

    if lib_key == "street_address":
        return CountryFieldDefinition(
            key="address_line_1",
            label="Address line 1",
            required=required,
        )

    if lib_key == "city":
        label = _CITY_TYPE_LABELS.get(rules.city_type or "", "City")
        return CountryFieldDefinition(key="city", label=label, required=required)

    if lib_key == "country_area":
        label = _AREA_TYPE_LABELS.get(rules.country_area_type or "", "Region")
        choices = rules.country_area_choices
        options = _deduplicate_choices(choices) if choices else None
        return CountryFieldDefinition(key="region", label=label, required=required, options=options)

    if lib_key == "postal_code":
        label = _POSTAL_TYPE_LABELS.get(rules.postal_code_type or "", "Postal code")
        matchers = rules.postal_code_matchers
        pattern = matchers[0].pattern if matchers else None
        return CountryFieldDefinition(
            key="postal_code", label=label, required=required, pattern=pattern
        )

    return None


def _deduplicate_choices(choices: list[tuple[str, str]]) -> list[CountrySubdivision]:
    """Deduplicate subdivision choices by code; first name for each code wins."""
    seen: set[str] = set()
    result: list[CountrySubdivision] = []
    for code, name in choices:
        if code not in seen:
            seen.add(code)
            result.append(CountrySubdivision(code=code, label=name))
    return result
