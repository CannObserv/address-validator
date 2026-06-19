"""Drift guard for the response-warning catalogue (GH #131).

Asserts bidirectional sync between the single source of truth
(:mod:`address_validator.core.warnings`) and the consumer-facing catalogue
(``docs/WARNINGS.md``):

- every warning template defined in code is documented, and
- every documented template maps back to a live code template.

If you add or change a response warning, update both ``core/warnings.py`` and
``docs/WARNINGS.md`` — this test fails on any divergence.
"""

import ast
import re
from pathlib import Path

from address_validator.core import warnings as warning_catalogue

REPO_ROOT = Path(__file__).resolve().parents[2]
WARNINGS_DOC = REPO_ROOT / "docs" / "WARNINGS.md"
SRC_ROOT = REPO_ROOT / "src" / "address_validator"
WARNINGS_MODULE = SRC_ROOT / "core" / "warnings.py"


def _documented_templates() -> set[str]:
    """Extract warning templates from the backtick-quoted first column of the
    catalogue table in ``docs/WARNINGS.md``."""
    text = WARNINGS_DOC.read_text(encoding="utf-8")
    templates: set[str] = set()
    for row in re.finditer(r"^\|\s*`([^`]+)`\s*\|", text, flags=re.MULTILINE):
        templates.add(row.group(1))
    return templates


def _module_string_constants() -> dict[str, str]:
    """Every module-level upper-case ``str`` constant in ``core/warnings.py``."""
    return {
        name: value
        for name, value in vars(warning_catalogue).items()
        if name.isupper() and isinstance(value, str)
    }


def _is_str_literal(node: ast.AST) -> bool:
    """A string ``Constant`` or an f-string (``JoinedStr``)."""
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _contains_str_literal(node: ast.AST) -> bool:
    """True if *node* is a str literal, or a list/tuple/set holding one."""
    if _is_str_literal(node):
        return True
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return any(_is_str_literal(elt) for elt in node.elts)
    return False


def _targets_warnings(node: ast.AST) -> bool:
    """True if *node* refers to a ``warnings`` list — either a bare name
    ``warnings`` or any attribute access ending in ``.warnings`` (e.g.
    ``result.warnings``, ``std.warnings``)."""
    return (isinstance(node, ast.Name) and node.id == "warnings") or (
        isinstance(node, ast.Attribute) and node.attr == "warnings"
    )


def _inline_warning_literals() -> list[str]:
    """Find any string literal that flows into a ``warnings`` list anywhere
    under ``src/`` — these bypass the catalogue and must not exist.

    Covers the realistic vectors:
    - ``warnings.append("...")`` / ``.append(f"...")``
    - ``warnings.extend([...])``
    - ``warnings = ["...", ...]`` (incl. annotated assignment)
    - ``Response(warnings=["..."])`` keyword argument
    - ``{"warnings": ["..."]}`` dict literal (e.g. ``model_copy`` update)
    """
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if path == WARNINGS_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _flows_str_literal_into_warnings(node):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{node.lineno}")
    return violations


def _flows_str_literal_into_warnings(node: ast.AST) -> bool:
    """True if *node* puts a string literal into a ``warnings`` list."""
    # warnings.append(<literal>) / warnings.extend([<literal>, ...])
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("append", "extend")
        and _targets_warnings(node.func.value)
        and node.args
    ):
        return _contains_str_literal(node.args[0])
    # The value bound to a `warnings` target, via the supported binding forms.
    bound_value: ast.AST | None = None
    if isinstance(node, ast.Assign) and any(_targets_warnings(t) for t in node.targets):
        bound_value = node.value  # warnings = [<literal>, ...]
    elif isinstance(node, ast.AnnAssign) and _targets_warnings(node.target):
        bound_value = node.value  # warnings: list[str] = [<literal>, ...]
    elif isinstance(node, ast.keyword) and node.arg == "warnings":
        bound_value = node.value  # foo(warnings=[<literal>, ...])
    if bound_value is not None:
        return _contains_str_literal(bound_value)
    # {"warnings": [<literal>, ...]} dict literal (e.g. model_copy update)
    if isinstance(node, ast.Dict):
        return any(
            isinstance(k, ast.Constant) and k.value == "warnings" and _contains_str_literal(v)
            for k, v in zip(node.keys, node.values, strict=True)
            if k is not None
        )
    return False


def test_doc_exists() -> None:
    assert WARNINGS_DOC.is_file(), f"missing catalogue doc: {WARNINGS_DOC}"


def test_catalogue_is_nonempty() -> None:
    assert warning_catalogue.CATALOGUE, "CATALOGUE must list every response warning"


def test_every_code_warning_is_documented() -> None:
    documented = _documented_templates()
    missing = [w for w in warning_catalogue.CATALOGUE if w not in documented]
    assert not missing, f"warnings defined in code but absent from docs/WARNINGS.md: {missing}"


def test_every_documented_warning_exists_in_code() -> None:
    documented = _documented_templates()
    catalogue = set(warning_catalogue.CATALOGUE)
    orphaned = [w for w in documented if w not in catalogue]
    assert not orphaned, f"warnings documented but absent from core/warnings.py: {orphaned}"


def test_every_module_constant_is_in_catalogue() -> None:
    """A warning string constant added to the module but omitted from
    ``CATALOGUE`` would ship undocumented — guard against it."""
    catalogue = set(warning_catalogue.CATALOGUE)
    missing = {
        name: value for name, value in _module_string_constants().items() if value not in catalogue
    }
    assert not missing, f"warning constants missing from CATALOGUE: {sorted(missing)}"


def test_no_inline_warning_literals_in_src() -> None:
    """All response warnings must come from ``core/warnings.py`` — no call site
    may append a bare string/f-string literal to a ``warnings`` list."""
    violations = _inline_warning_literals()
    assert not violations, (
        f"inline warning literals found (use core/warnings.py constants instead): {violations}"
    )
