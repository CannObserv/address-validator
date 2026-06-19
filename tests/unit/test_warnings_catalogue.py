"""Drift guard for the response-warning catalogue (GH #131).

Asserts bidirectional sync between the single source of truth
(:mod:`address_validator.core.warnings`) and the consumer-facing catalogue
(``docs/WARNINGS.md``):

- every warning template defined in code is documented, and
- every documented template maps back to a live code template.

If you add or change a response warning, update both ``core/warnings.py`` and
``docs/WARNINGS.md`` — this test fails on any divergence.
"""

import re
from pathlib import Path

from address_validator.core import warnings as warning_catalogue

WARNINGS_DOC = Path(__file__).resolve().parents[2] / "docs" / "WARNINGS.md"


def _documented_templates() -> set[str]:
    """Extract warning templates from the backtick-quoted first column of the
    catalogue table in ``docs/WARNINGS.md``."""
    text = WARNINGS_DOC.read_text(encoding="utf-8")
    templates: set[str] = set()
    for row in re.finditer(r"^\|\s*`([^`]+)`\s*\|", text, flags=re.MULTILINE):
        templates.add(row.group(1))
    return templates


def test_doc_exists():
    assert WARNINGS_DOC.is_file(), f"missing catalogue doc: {WARNINGS_DOC}"


def test_catalogue_is_nonempty():
    assert warning_catalogue.CATALOGUE, "CATALOGUE must list every response warning"


def test_every_code_warning_is_documented():
    documented = _documented_templates()
    missing = [w for w in warning_catalogue.CATALOGUE if w not in documented]
    assert not missing, f"warnings defined in code but absent from docs/WARNINGS.md: {missing}"


def test_every_documented_warning_exists_in_code():
    documented = _documented_templates()
    catalogue = set(warning_catalogue.CATALOGUE)
    orphaned = [w for w in documented if w not in catalogue]
    assert not orphaned, f"warnings documented but absent from core/warnings.py: {orphaned}"
