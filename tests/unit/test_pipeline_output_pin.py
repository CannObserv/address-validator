"""Golden-corpus drift test for PIPELINE_CODE_VERSION (#145).

Pins a hash of parse → standardize output over a fixed US-address corpus. If a code
change alters pipeline output, the hash changes and this test fails — forcing the
PIPELINE_CODE_VERSION bump that invalidates stale cache rows. This is the mechanical
enforcement of the "discipline to bump the constant" cost accepted in #145; without
it, an unbumped output change silently serves stale ``validated_addresses`` rows.

On failure, update BOTH pins below in the same commit:

1. Bump ``PIPELINE_CODE_VERSION`` in ``src/address_validator/core/pipeline_version.py``
2. Set ``_PINNED_CODE_VERSION`` to the new value and ``_PINNED_CORPUS_HASH`` to the
   new hash printed in the assertion message

Scope: US path only (usaddress bundled model + standardizer). The CA/libpostal
sidecar and custom CRF models are outside this corpus — custom models are covered
by the runtime model fingerprint instead (see core/pipeline_version.py docstring).
"""

import hashlib
import json

import pycrfsuite
import pytest
import usaddress

from address_validator.core.pipeline_version import PIPELINE_CODE_VERSION
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize

# ---------------------------------------------------------------------------
# Pins — update together with PIPELINE_CODE_VERSION (see module docstring)
# ---------------------------------------------------------------------------

_PINNED_CODE_VERSION = 1
_PINNED_CORPUS_HASH = "446138393213f9a9a0b94c8c139ef81e77a2314fdc7b56f5ec6044e28d1e1523"

# Fixed corpus — exercises the pipeline surfaces most likely to change output:
# cleanup regexes, directional/type abbreviation, secondary units, PO Box / rural
# route formats, casing, ZIP+4, recovery paths, and unparseable place names.
# NEVER edit existing entries (that silently re-baselines history); append only,
# which changes the hash and forces a deliberate re-pin + version bump.
_CORPUS = [
    "123 Main St, Springfield IL 62701",
    "123 main street springfield illinois 62701",
    "500 West 5th Avenue Apt 3B, Anytown NY 10001",
    "PO Box 1234, Olympia WA 98501",
    "P.O. Box 42 Portland OR 97201",
    "1600 pennsylvania ave nw washington dc 20500",
    "742 Evergreen Terrace  Springfield OR",
    "RR 2 Box 152 Glennallen AK 99588",
    "1000 Highway 9 N Ben Lomond CA 95005",
    "55 Water Street Suite 200 Brooklyn NY 11201",
    "9800 Fredericksburg Rd San Antonio TX 78288",
    "350 fifth avenue, floor 34, new york, ny 10118",
    "123 N Main St Apt #4, Salt Lake City, UT 84101-1234",
    "456 Oak Ln (rear entrance) Boise ID 83702",
    "The Grove, Los Angeles CA",
    "1/2 421 Elm St Tampa FL 33602",
]


async def _corpus_hash() -> str:
    """Deterministic sha256 over the full parse → standardize output of the corpus."""
    outputs = []
    for raw in _CORPUS:
        outcome = await parse_address(raw, country="US")
        parsed = outcome.response
        std = standardize(parsed.components.values, country="US", upstream_warnings=parsed.warnings)
        outputs.append(
            {
                "input": raw,
                "parse_type": outcome.parse_type,
                "components": dict(sorted(std.components.values.items())),
                "address_line_1": std.address_line_1,
                "address_line_2": std.address_line_2,
                "city": std.city,
                "region": std.region,
                "postal_code": std.postal_code,
                "standardized": std.standardized,
                "warnings": std.warnings,
            }
        )
    payload = json.dumps(outputs, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class TestPipelineOutputPin:
    async def test_corpus_hash_matches_pin(self) -> None:
        actual = await _corpus_hash()
        assert actual == _PINNED_CORPUS_HASH, (
            "Pipeline output changed over the golden corpus.\n"
            "If this change is intentional:\n"
            "  1. Bump PIPELINE_CODE_VERSION in core/pipeline_version.py\n"
            f"  2. Re-pin in {__name__}: _PINNED_CODE_VERSION = <new version>, "
            f"_PINNED_CORPUS_HASH = '{actual}'\n"
            "Both pins and the bump belong in the same commit — the bump is what "
            "invalidates stale validated_addresses cache rows (#145)."
        )

    def test_code_version_matches_pin(self) -> None:
        assert PIPELINE_CODE_VERSION == _PINNED_CODE_VERSION, (
            "PIPELINE_CODE_VERSION and _PINNED_CODE_VERSION are out of sync. "
            "When bumping the pipeline version, update the pin (and corpus hash) "
            "in this file in the same commit."
        )

    def test_corpus_is_append_only_floor(self) -> None:
        """Guard against pruning the corpus to dodge a hash mismatch."""
        assert len(_CORPUS) >= 16


@pytest.fixture(autouse=True)
def _force_bundled_model():
    """Pin the corpus to the bundled model regardless of session state.

    Deleting CUSTOM_MODEL_PATH is not enough: ``parse_address`` reads the
    module-global ``usaddress.TAGGER``, and any earlier lifespan-running test
    (``with TestClient(...)`` → ``load_custom_model()``) may have swapped it
    for the session when the env var was exported. Force a tagger opened on
    the bundled model for the duration of this module's tests.
    """
    original = usaddress.TAGGER
    bundled = pycrfsuite.Tagger()
    bundled.open(usaddress.MODEL_PATH)
    usaddress.TAGGER = bundled
    yield
    usaddress.TAGGER = original
    bundled.close()
