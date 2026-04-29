"""Unit tests for the country format service."""

import re
from unittest.mock import patch

from address_validator.services.country_format import get_country_format


class TestGetCountryFormat:
    def test_us_returns_five_fields(self) -> None:
        result = get_country_format("US")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert keys == [
            "address_line_1",
            "address_line_2",
            "city",
            "region",
            "postal_code",
        ]

    def test_us_country_field(self) -> None:
        result = get_country_format("US")
        assert result is not None
        assert result.country == "US"

    def test_us_region_label_is_state(self) -> None:
        result = get_country_format("US")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.label == "State"

    def test_us_region_has_options(self) -> None:
        result = get_country_format("US")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.options is not None
        codes = [o.code for o in region.options]
        assert "CA" in codes
        assert "NY" in codes
        labels = [o.label for o in region.options]
        assert "California" in labels

    def test_us_postal_code_label_is_zip(self) -> None:
        result = get_country_format("US")
        assert result is not None
        postal = next(f for f in result.fields if f.key == "postal_code")
        assert postal.label == "ZIP code"

    def test_us_postal_code_has_pattern(self) -> None:
        result = get_country_format("US")
        assert result is not None
        postal = next(f for f in result.fields if f.key == "postal_code")
        assert postal.pattern is not None
        assert re.match(postal.pattern, "95014")

    def test_us_address_line_2_is_optional(self) -> None:
        result = get_country_format("US")
        assert result is not None
        line2 = next(f for f in result.fields if f.key == "address_line_2")
        assert line2.required is False

    def test_ca_region_label_is_province(self) -> None:
        result = get_country_format("CA")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.label == "Province"

    def test_ca_options_deduplicated(self) -> None:
        # CA has bilingual names — each code should appear once
        result = get_country_format("CA")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.options is not None
        codes = [o.code for o in region.options]
        assert len(codes) == len(set(codes)), "duplicate codes found"
        # BC appears twice in raw data (English + French name) — only once here
        assert codes.count("BC") == 1

    def test_ca_postal_code_label_is_postal(self) -> None:
        result = get_country_format("CA")
        assert result is not None
        postal = next(f for f in result.fields if f.key == "postal_code")
        assert postal.label == "Postal code"

    def test_gb_no_region_field(self) -> None:
        # GB address format does not include country_area (%S)
        result = get_country_format("GB")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert "region" not in keys

    def test_gb_field_order(self) -> None:
        result = get_country_format("GB")
        assert result is not None
        keys = [f.key for f in result.fields]
        # GB format: %A %C %Z  (street, city, postal)
        assert keys.index("address_line_1") < keys.index("city")
        assert keys.index("city") < keys.index("postal_code")

    def test_hk_region_before_street(self) -> None:
        # HK format: %S %C %A  (region, city, street)
        result = get_country_format("HK")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert keys.index("region") < keys.index("address_line_1")

    def test_country_with_no_postal_code_pattern(self) -> None:
        # HK has no postal code at all — postal_code field absent
        result = get_country_format("HK")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert "postal_code" not in keys

    def test_returns_none_for_library_error(self) -> None:
        with patch(
            "address_validator.services.country_format.get_validation_rules",
            side_effect=ValueError("bad code"),
        ):
            result = get_country_format("ZZ")
        assert result is None
