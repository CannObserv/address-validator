"""Unit tests for Pydantic request/response models in models.py."""

import pytest
from pydantic import ValidationError

from address_validator.models import StandardizeRequestV1, ValidateRequestV1


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
