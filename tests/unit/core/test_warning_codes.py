"""Resolver from response-warning prose to machine-readable codes (GH #338).

``resolve_warning_codes`` is the single point where ``warnings[i]`` becomes
``warning_codes[i]``. These tests pin that it is total over the catalogue,
unambiguous whatever text a template interpolates, index-aligned, and that an
unmatched string never reaches the logs.
"""

import itertools
import logging
import string

import pytest

from address_validator.core import warnings as warning_catalogue
from address_validator.core.warning_codes import (
    code_for,
    fixed_head,
    resolve_warning_codes,
)
from address_validator.core.warnings import WARNING_CODES, WarningCode


def _fields(template: str) -> list[str]:
    return [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]


TEMPLATED = [t for t in warning_catalogue.CATALOGUE if _fields(t)]
STATIC = [t for t in warning_catalogue.CATALOGUE if not _fields(t)]

# Interpolated values that could confuse a naive matcher: empty, quotes,
# braces, newlines (input parentheticals keep inner newlines), regex
# metacharacters, and the full text of every other catalogued warning.
SAMPLE_VALUES = [
    "",
    "X",
    "STE 100 & 101",
    "it's",
    "'",
    "{text}",
    "{{",
    "rear\nentrance",
    "trailing\n",
    ".*+?()[]$^\\",
    *warning_catalogue.CATALOGUE,
]


def test_catalogue_has_static_and_templated_warnings() -> None:
    # Guards the parametrisation below against silently testing nothing.
    assert TEMPLATED
    assert STATIC


@pytest.mark.parametrize("template", STATIC)
def test_static_warning_resolves_to_its_code(template: str) -> None:
    assert code_for(template) is WARNING_CODES[template]


@pytest.mark.parametrize("template", TEMPLATED)
@pytest.mark.parametrize("value", SAMPLE_VALUES)
def test_templated_warning_resolves_to_its_own_code(template: str, value: str) -> None:
    formatted = template.format(**dict.fromkeys(_fields(template), value))
    assert code_for(formatted) is WARNING_CODES[template]


def test_no_fixed_head_prefixes_another() -> None:
    """The invariant that makes matching unambiguous: if no warning's fixed
    head (static text, or a template's literal text before its first field)
    is a prefix of another's, any string matches at most one entry, whatever
    is interpolated. A new warning that breaks this must be reworded."""
    heads = {t: fixed_head(t) for t in warning_catalogue.CATALOGUE}
    collisions = [
        (a, b) for a, b in itertools.permutations(heads, 2) if heads[b].startswith(heads[a])
    ]
    assert not collisions, f"warning heads overlap; matching would be ambiguous: {collisions}"


def test_fixed_head_of_template_stops_at_first_field() -> None:
    assert fixed_head("Only unit '{unit}' of '{line2}'") == "Only unit '"
    assert fixed_head("No fields here") == "No fields here"
    assert fixed_head("Brace {{literal}} then '{x}'") == "Brace {literal} then '"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "something else entirely",
        warning_catalogue.REPEATED_LABELS + " ",  # near-miss static
        warning_catalogue.REPEATED_LABELS + "\n",  # `$` would accept this
        "Parenthesized text removed: 'unterminated",  # templated, missing tail
        "Validation undetermined while a fallback provider was unreachable; "
        "a later retry may produce a determination",  # pre-#275 wording
    ],
)
def test_non_catalogued_text_has_no_code(text: str) -> None:
    assert code_for(text) is None


def test_resolve_is_index_aligned() -> None:
    warnings = [
        warning_catalogue.PARENTHESIZED_REMOVED.format(text="rear\nentrance"),
        warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE,
        warning_catalogue.UNRECOGNIZED_REGION.format(region="ZZ"),
    ]
    assert resolve_warning_codes(warnings) == [
        WarningCode.PARENTHESIZED_REMOVED,
        WarningCode.PROVIDER_FALLBACK_UNREACHABLE,
        WarningCode.UNRECOGNIZED_REGION,
    ]


def test_resolve_empty_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="address_validator.core.warning_codes"):
        assert resolve_warning_codes([]) == []
    assert not caplog.records


def test_unmatched_gets_sentinel_and_logs_count_only(caplog: pytest.LogCaptureFixture) -> None:
    secret_a = "123 SECRET ST APT 9"
    secret_b = "PII-MARKER-42"
    warnings = [secret_a, warning_catalogue.PROVIDER_INFERRED, secret_b]

    with caplog.at_level(logging.DEBUG, logger="address_validator.core.warning_codes"):
        codes = resolve_warning_codes(warnings)

    assert codes == [
        WarningCode.UNRECOGNIZED,
        WarningCode.PROVIDER_INFERRED,
        WarningCode.UNRECOGNIZED,
    ]
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert "2" in record.getMessage()
    # Never the text: templated warnings carry address fragments (#252).
    for rendered in (record.getMessage(), repr(record.args), repr(record.__dict__)):
        assert secret_a not in rendered
        assert secret_b not in rendered


def test_sentinel_is_not_any_warnings_code() -> None:
    assert WarningCode.UNRECOGNIZED not in WARNING_CODES.values()
