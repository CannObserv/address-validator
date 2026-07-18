"""Address parsing service using the usaddress library."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import usaddress

from address_validator.core import warnings as warning_catalogue
from address_validator.models import ComponentSet, ParseResponseV2
from address_validator.services.audit import set_audit_context
from address_validator.services.libpostal_client import (
    LibpostalClient,
    LibpostalUnavailableError,
)
from address_validator.services.parse_recovery import (
    RecoveryKind,
    collect_ambiguous_components,
    recover_components,
)
from address_validator.services.training_candidates import set_candidate_data
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseOutcome:
    """Result of :func:`parse_address`, plus request-scoped side-effect data.

    ``parse_address`` is a pure parse — it performs no ContextVar writes so it
    is safe to call outside a live request (training scripts, batch tooling,
    tests).  Callers running inside a request scope (routers, the validation
    pipeline) use the extra fields here to replicate the audit /
    training-candidate ContextVar writes the parser used to perform itself:

    - ``parse_type`` → ``set_audit_context(parse_type=...)``.  This is the value
      the parser previously fed to the audit context and is NOT always equal to
      ``response.type`` (the CA/libpostal path sets ``"libpostal"`` while the
      response type is ``"Street Address"``).
    - ``candidate_data`` → ``set_candidate_data(**candidate_data)`` when not
      ``None``.  ``None`` means no candidate should be recorded for this parse
      (preserving the prior conditional behaviour on the clean US path).

    ``candidate_data`` is deliberately kept off the public ``ParseResponseV2``
    so training/candidate metadata never leaks into the API contract / OpenAPI.
    """

    response: ParseResponseV2
    parse_type: str
    candidate_data: dict[str, Any] | None = field(default=None)


# Recovery kinds that mark a clean parse as a training candidate: the parser
# mis-tagged something badly enough that a heuristic had to repair it, so the
# raw input is worth labelling for CRF retraining.  DUPLICATE_UNIT_COLLAPSED
# is excluded — a data-entry duplicate is an input problem, not a model one.
_CANDIDATE_RECOVERY_KINDS = frozenset(
    {RecoveryKind.UNIT_RECOVERED, RecoveryKind.FRAGMENT_RECOVERED}
)

# Map usaddress tag names to friendlier keys.
TAG_NAMES: dict[str, str] = {
    "AddressNumber": "premise_number",
    "AddressNumberPrefix": "premise_number_prefix",
    "AddressNumberSuffix": "premise_number_suffix",
    "StreetNamePreDirectional": "thoroughfare_pre_direction",
    "StreetNamePreModifier": "thoroughfare_pre_modifier",
    "StreetNamePreType": "thoroughfare_leading_type",
    "StreetName": "thoroughfare_name",
    "StreetNamePostDirectional": "thoroughfare_post_direction",
    "StreetNamePostModifier": "thoroughfare_post_modifier",
    "StreetNamePostType": "thoroughfare_trailing_type",
    "SubaddressType": "dependent_sub_premise_type",
    "SubaddressIdentifier": "dependent_sub_premise_number",
    "OccupancyType": "sub_premise_type",
    "OccupancyIdentifier": "sub_premise_number",
    "PlaceName": "locality",
    "StateName": "administrative_area",
    "ZipCode": "postcode",
    "USPSBoxType": "general_delivery_type",
    "USPSBoxID": "general_delivery",
    "USPSBoxGroupType": "general_delivery_group_type",
    "USPSBoxGroupID": "general_delivery_group",
    "BuildingName": "premise_name",
    "Recipient": "addressee",
    "NotAddress": "not_address",
    "IntersectionSeparator": "intersection_separator",
    "LandmarkName": "landmark",
    "CornerOf": "corner_of",
    # Second street (intersections)
    "SecondStreetName": "second_thoroughfare_name",
    "SecondStreetNamePreDirectional": "second_thoroughfare_pre_direction",
    "SecondStreetNamePreModifier": "second_thoroughfare_pre_modifier",
    "SecondStreetNamePreType": "second_thoroughfare_leading_type",
    "SecondStreetNamePostDirectional": "second_thoroughfare_post_direction",
    "SecondStreetNamePostModifier": "second_thoroughfare_post_modifier",
    "SecondStreetNamePostType": "second_thoroughfare_trailing_type",
}


def apply_parse_side_effects(outcome: ParseOutcome) -> None:
    """Replay a :class:`ParseOutcome`'s request-scoped ContextVar writes.

    ``parse_address`` no longer performs these writes itself (so it stays a pure
    parse usable outside a request).  Request-scoped callers — the v2 routers and
    the validation pipeline — call this immediately after ``parse_address`` so
    the audit middleware sees exactly the same ContextVar state it saw before the
    side effects were lifted out of the parser:

    - ``set_audit_context(parse_type=outcome.parse_type)`` — always.
    - ``set_candidate_data(**outcome.candidate_data)`` — only when the parser
      produced candidate data (``None`` on the clean US and CA paths).

    This helper is the single coupling point between the parser module and the
    audit/training ContextVar machinery; ``parse_address`` itself touches
    neither.
    """
    set_audit_context(parse_type=outcome.parse_type)
    if outcome.candidate_data is not None:
        set_candidate_data(**outcome.candidate_data)


async def parse_address(
    raw: str,
    country: str = "US",
    libpostal_client: LibpostalClient | None = None,
) -> ParseOutcome:
    """Parse *raw* address string into labelled components.

    This is a **pure parse**: it performs no request-scoped ContextVar writes,
    so it is safe to call outside a live request.  It returns a
    :class:`ParseOutcome` wrapping the public ``ParseResponseV2`` plus the data
    a request-scoped caller needs to set the audit / training-candidate
    ContextVars itself (``parse_type`` and ``candidate_data``).

    For ``country="CA"``, delegates to the libpostal sidecar via
    *libpostal_client*.  Raises ``LibpostalUnavailableError`` (→ 503)
    when the client is None or unreachable.

    For ``country="US"``, uses the existing usaddress path unchanged.
    The *libpostal_client* parameter is ignored for US addresses.
    """
    if country == "CA":
        if libpostal_client is None:
            raise LibpostalUnavailableError("No libpostal client configured")
        components = await libpostal_client.parse(raw)
        response = ParseResponseV2(
            input=raw,
            country=country,
            components=ComponentSet(
                spec="raw",
                spec_version="1",
                values=components,
            ),
            type="Street Address",
            warnings=[],
        )
        # parse_type differs from response.type on this path ("libpostal"
        # vs "Street Address"); the CA path never collects a candidate.
        return ParseOutcome(response=response, parse_type="libpostal", candidate_data=None)
    return _parse(raw, country)


def _parse(raw: str, country: str) -> ParseOutcome:
    """Parse *raw* address string into labelled components.

    Returns a :class:`ParseOutcome` whose ``response`` is a
    :class:`ParseResponseV2` with:
      - ``input``: the original string
      - ``components``: dict of component_name -> value
      - ``type``: ``"Street Address"``, ``"Intersection"``, or ``"Ambiguous"``

    and whose ``parse_type`` / ``candidate_data`` carry the request-scoped
    side-effect payloads for the caller to apply.
    """
    warnings: list[str] = []

    # USPS Pub 28 §354: parentheses are not valid in standardised
    # addresses.  Parenthesized text is typically wayfinding notes
    # (e.g. "(EAST)", "(UPPER LEVEL)") that confuse usaddress.  Strip
    # it before parsing and collapse any resulting extra whitespace.
    paren_matches = re.findall(r"\([^)]*\)", raw)
    cleaned = re.sub(r"\([^)]*\)", "", raw)
    # Strip any remaining unmatched parentheses (e.g. "123 Main) St").
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    for match in paren_matches:
        inner = match[1:-1].strip()
        if inner:
            warnings.append(warning_catalogue.PARENTHESIZED_REMOVED.format(text=inner))

    try:
        tagged, addr_type = usaddress.tag(cleaned)
    except usaddress.RepeatedLabelError as exc:
        logger.warning("ambiguous parse: repeated labels in input")
        component_values: dict[str, str] = collect_ambiguous_components(
            exc.parsed_string, warnings, TAG_NAMES
        )
        recover_components(component_values, warnings)

        candidate_data = {
            "raw_address": raw,
            "failure_type": "repeated_label_error",
            "parsed_tokens": list(exc.parsed_string),
            "recovered_components": component_values,
            "failure_reason": f"usaddress.RepeatedLabelError: {exc}".replace("\n", " ")[:400],
        }

        logger.debug("parsed address type=Ambiguous country=%s", country)
        response = ParseResponseV2(
            input=raw,
            country=country,
            components=ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
                values=component_values,
            ),
            type="Ambiguous",
            warnings=warnings,
        )
        return ParseOutcome(
            response=response, parse_type="Ambiguous", candidate_data=candidate_data
        )

    logger.debug("parsed address type=%s country=%s", addr_type, country)
    component_values = {TAG_NAMES.get(label, label): value for label, value in tagged.items()}

    recovery_events = recover_components(component_values, warnings)

    candidate_data: dict[str, Any] | None = None
    candidate_events = [e for e in recovery_events if e.kind in _CANDIDATE_RECOVERY_KINDS]
    if candidate_events:
        candidate_data = {
            "raw_address": raw,
            "failure_type": "post_parse_recovery",
            "parsed_tokens": [(v, k) for k, v in tagged.items()],
            "recovered_components": component_values,
            "failure_reason": "; ".join(e.warning for e in candidate_events)[:400],
        }

    response = ParseResponseV2(
        input=raw,
        country=country,
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values=component_values,
        ),
        type=addr_type,
        warnings=warnings,
    )
    return ParseOutcome(response=response, parse_type=addr_type, candidate_data=candidate_data)
