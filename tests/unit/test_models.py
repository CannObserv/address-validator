"""Unit tests for Pydantic request/response models in models.py."""

import pytest
from pydantic import ValidationError

from address_validator.models import (
    CountryFieldDefinition,
    CountryFormatResponse,
    CountrySubdivision,
    StandardizeRequestV1,
    ValidateRequestV1,
)


class TestStandardizeRequestV1Model:
    def test_accepts_raw_address_string(self) -> None:
        req = StandardizeRequestV1(address="123 Main St, Springfield, IL 62701")
        assert req.address == "123 Main St, Springfield, IL 62701"
        assert req.components is None

    def test_accepts_components_dict(self) -> None:
        req = StandardizeRequestV1(components={"address_number": "123", "street_name": "MAIN"})
        assert req.components == {"address_number": "123", "street_name": "MAIN"}
        assert req.address is None

    def test_both_fields_none_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            StandardizeRequestV1()

    def test_blank_address_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            StandardizeRequestV1(address="   ")

    def test_country_defaults_to_us(self) -> None:
        req = StandardizeRequestV1(address="123 Main St")
        assert req.country == "US"


class TestValidateRequestV1Model:
    def test_accepts_raw_address_string(self) -> None:
        req = ValidateRequestV1(address="123 Main St, Springfield, IL 62701")
        assert req.address == "123 Main St, Springfield, IL 62701"
        assert req.components is None

    def test_accepts_components_dict(self) -> None:
        req = ValidateRequestV1(components={"address_number": "123", "street_name": "MAIN"})
        assert req.components == {"address_number": "123", "street_name": "MAIN"}
        assert req.address is None

    def test_both_fields_none_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ValidateRequestV1()

    def test_blank_address_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ValidateRequestV1(address="   ")

    def test_country_defaults_to_us(self) -> None:
        req = ValidateRequestV1(address="123 Main St")
        assert req.country == "US"


def test_country_format_models_exist() -> None:
    sub = CountrySubdivision(code="AB", label="Alberta")
    assert sub.code == "AB"
    assert sub.label == "Alberta"

    field = CountryFieldDefinition(key="region", label="Province", required=True)
    assert field.options is None
    assert field.pattern is None

    field_with_opts = CountryFieldDefinition(
        key="region",
        label="Province",
        required=True,
        options=[sub],
    )
    assert len(field_with_opts.options) == 1

    resp = CountryFormatResponse(country="CA", fields=[field])
    assert resp.country == "CA"
    assert len(resp.fields) == 1
