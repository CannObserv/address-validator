"""Resolve response-warning prose to machine-readable codes (GH #338).

``resolve_warning_codes`` is the **only** place a ``warnings[i]`` string becomes
``warning_codes[i]``. Responses compute their codes through it at serialization,
so a fresh answer and a cache hit (which stores prose only, in
``validated_addresses.warnings_json``) always yield the same codes, and a
``model_copy(update={"warnings": ...})`` can't leave codes stale.

Matching, built once at import from :data:`core.warnings.WARNING_CODES`:

- a static warning is an exact dict lookup;
- a template is tokenised with :class:`string.Formatter` (so ``{{``/``}}``
  escapes stay literal), its literal text escaped, each field matched by
  ``.*`` under ``re.DOTALL``, and the whole string checked with ``fullmatch``.
  ``DOTALL`` matters: an input parenthetical keeps inner newlines, so
  ``PARENTHESIZED_REMOVED`` can interpolate one.

Matching needs no ordering: no warning's :func:`fixed_head` is a prefix of
another's (a test enforces it), so at most one entry can match any string,
whatever the interpolated text.
"""

import logging
import re
import string
from collections.abc import Sequence

from address_validator.core.warnings import WARNING_CODES, WarningCode

logger = logging.getLogger(__name__)

_FORMATTER = string.Formatter()


def fixed_head(template: str) -> str:
    """Literal text of *template* before its first field (all of it if none)."""
    head = []
    for literal, field, _spec, _conv in _FORMATTER.parse(template):
        head.append(literal)
        if field is not None:
            break
    return "".join(head)


def _pattern(template: str) -> re.Pattern[str] | None:
    """A ``fullmatch`` pattern for *template*, or ``None`` if it has no field."""
    parts: list[str] = []
    has_field = False
    for literal, field, _spec, _conv in _FORMATTER.parse(template):
        parts.append(re.escape(literal))
        if field is not None:
            parts.append(".*")
            has_field = True
    return re.compile("".join(parts), re.DOTALL) if has_field else None


def _build() -> tuple[dict[str, WarningCode], tuple[tuple[re.Pattern[str], WarningCode], ...]]:
    static: dict[str, WarningCode] = {}
    templated: list[tuple[re.Pattern[str], WarningCode]] = []
    for template, code in WARNING_CODES.items():
        pattern = _pattern(template)
        if pattern is None:
            static[template] = code
        else:
            templated.append((pattern, code))
    return static, tuple(templated)


_STATIC, _TEMPLATED = _build()


def code_for(warning: str) -> WarningCode | None:
    """The code of one warning string, or ``None`` if no template matches."""
    code = _STATIC.get(warning)
    if code is not None:
        return code
    for pattern, templated_code in _TEMPLATED:
        if pattern.fullmatch(warning):
            return templated_code
    return None


def resolve_warning_codes(warnings: Sequence[str]) -> list[WarningCode]:
    """Codes for *warnings*, same length and order.

    A string no template matches gets :attr:`WarningCode.UNRECOGNIZED`, keeping
    alignment, and one WARNING reports how many. The text itself is **never**
    logged: templated warnings carry address fragments, and WARNING lines leave
    the host in unit-failure tails (#252).
    """
    codes: list[WarningCode] = []
    unmatched = 0
    for warning in warnings:
        code = code_for(warning)
        if code is None:
            unmatched += 1
            code = WarningCode.UNRECOGNIZED
        codes.append(code)
    if unmatched:
        logger.warning(
            "warning_codes: %d warning(s) matched no catalogued template; coded %s",
            unmatched,
            WarningCode.UNRECOGNIZED.value,
        )
    return codes
