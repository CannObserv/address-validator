"""``warning_codes`` on every response model that carries ``warnings`` (GH #338)."""

import inspect

import pytest
from pydantic import BaseModel

from address_validator import models
from address_validator.core import warnings as warning_catalogue
from address_validator.core.warnings import WarningCode
from address_validator.main import app
from address_validator.models import (
    ComponentSet,
    ParseResponseV2,
    StandardizeResponseV2,
    ValidateResponseV2,
    ValidationResult,
)

_COMPONENTS = ComponentSet(spec="usps-pub28", spec_version="unknown", values={})
_WARNINGS = [
    warning_catalogue.PARENTHESIZED_REMOVED.format(text="rear entrance"),
    warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE,
]
_CODES = [WarningCode.PARENTHESIZED_REMOVED, WarningCode.PROVIDER_FALLBACK_UNREACHABLE]


def _response_models_with_warnings() -> list[type[BaseModel]]:
    return [
        cls
        for _, cls in inspect.getmembers(models, inspect.isclass)
        if issubclass(cls, BaseModel) and "warnings" in cls.model_fields
    ]


def _build(cls: type[BaseModel], warnings: list[str]) -> BaseModel:
    if cls is ParseResponseV2:
        return ParseResponseV2(
            input="x",
            country="US",
            components=_COMPONENTS,
            type="Street Address",
            warnings=warnings,
        )
    if cls is StandardizeResponseV2:
        return StandardizeResponseV2(
            address_line_1="",
            address_line_2="",
            city="",
            region="",
            postal_code="",
            country="US",
            standardized="",
            components=_COMPONENTS,
            warnings=warnings,
        )
    if cls is ValidateResponseV2:
        return ValidateResponseV2(
            country="US", validation=ValidationResult(status="confirmed"), warnings=warnings
        )
    raise AssertionError(f"no builder for {cls.__name__}; add one")


RESPONSE_MODELS = [ParseResponseV2, StandardizeResponseV2, ValidateResponseV2]


def test_every_model_with_warnings_has_warning_codes() -> None:
    """A new response model carrying ``warnings`` must also carry the codes."""
    found = _response_models_with_warnings()
    assert set(found) == set(RESPONSE_MODELS), "update RESPONSE_MODELS and _build()"
    missing = [c.__name__ for c in found if "warning_codes" not in c.model_computed_fields]
    assert not missing, f"models with warnings but no warning_codes: {missing}"


@pytest.mark.parametrize("cls", RESPONSE_MODELS)
def test_codes_aligned_in_dump_and_json(cls: type[BaseModel]) -> None:
    obj = _build(cls, _WARNINGS)
    assert obj.warning_codes == _CODES
    assert obj.model_dump()["warning_codes"] == _CODES
    assert obj.model_dump(mode="json")["warning_codes"] == [c.value for c in _CODES]


@pytest.mark.parametrize("cls", RESPONSE_MODELS)
def test_no_warnings_no_codes(cls: type[BaseModel]) -> None:
    assert _build(cls, []).model_dump(mode="json")["warning_codes"] == []


@pytest.mark.parametrize("cls", RESPONSE_MODELS)
def test_model_copy_recomputes_codes(cls: type[BaseModel]) -> None:
    """Warnings are amended via ``model_copy(update=...)`` in the validate
    path; the codes must follow, never go stale."""
    obj = _build(cls, _WARNINGS[:1])
    copied = obj.model_copy(update={"warnings": [*obj.warnings, _WARNINGS[1]]})
    assert copied.warning_codes == _CODES


@pytest.mark.parametrize("cls", RESPONSE_MODELS)
def test_dump_round_trips(cls: type[BaseModel]) -> None:
    """Rebuilding from a dump (which now contains ``warning_codes``) works."""
    obj = _build(cls, _WARNINGS)
    assert cls(**obj.model_dump()) == obj


@pytest.fixture(scope="module")
def schemas() -> dict:
    return app.openapi()["components"]["schemas"]


class TestOpenAPI:
    def test_enum_lists_every_code(self, schemas: dict) -> None:
        assert schemas["WarningCode"]["type"] == "string"
        assert schemas["WarningCode"]["enum"] == [c.value for c in WarningCode]

    @pytest.mark.parametrize("cls", RESPONSE_MODELS)
    def test_response_schema_has_warning_codes(self, schemas: dict, cls: type) -> None:
        schema = schemas[cls.__name__]
        prop = schema["properties"]["warning_codes"]
        assert prop["type"] == "array"
        assert prop["items"] == {"$ref": "#/components/schemas/WarningCode"}
        assert prop["readOnly"] is True
        assert "warning_codes" in schema["required"]
        assert "warnings" in prop["description"]
