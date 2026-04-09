"""Tests for the bilingual street component splitter."""

from address_validator.services.street_splitter import split_road


class TestEnglishTrailingType:
    def test_street_suffix_at_end(self) -> None:
        r = split_road("main st")
        assert r["thoroughfare_name"] == "MAIN"
        assert r["thoroughfare_trailing_type"] == "ST"
        assert "thoroughfare_leading_type" not in r

    def test_avenue_suffix(self) -> None:
        r = split_road("oak avenue")
        assert r["thoroughfare_name"] == "OAK"
        assert r["thoroughfare_trailing_type"] == "AVE"

    def test_trailing_directional_extracted(self) -> None:
        r = split_road("bloor street west")
        assert r["thoroughfare_name"] == "BLOOR"
        assert r["thoroughfare_trailing_type"] == "ST"
        assert r["thoroughfare_post_direction"] == "W"

    def test_pre_directional_extracted(self) -> None:
        r = split_road("north main street")
        assert r["thoroughfare_pre_direction"] == "N"
        assert r["thoroughfare_name"] == "MAIN"
        assert r["thoroughfare_trailing_type"] == "ST"


class TestFrenchLeadingType:
    def test_rue_leading(self) -> None:
        r = split_road("rue des lilas")
        assert r["thoroughfare_leading_type"] == "RUE"
        assert r["thoroughfare_name"] == "DES LILAS"
        assert "thoroughfare_trailing_type" not in r

    def test_boulevard_leading_with_directional(self) -> None:
        r = split_road("boulevard rené-lévesque ouest")
        assert r["thoroughfare_leading_type"] == "BLVD"
        assert r["thoroughfare_name"] == "RENÉ-LÉVESQUE"
        assert r["thoroughfare_post_direction"] == "O"

    def test_chemin_with_article(self) -> None:
        r = split_road("chemin de la côte-de-liesse")
        assert r["thoroughfare_leading_type"] == "CH"
        assert r["thoroughfare_name"] == "DE LA CÔTE-DE-LIESSE"

    def test_avenue_leading_with_du(self) -> None:
        r = split_road("avenue du parc")
        assert r["thoroughfare_leading_type"] == "AVE"
        assert r["thoroughfare_name"] == "DU PARC"

    def test_french_nord_est_directional(self) -> None:
        r = split_road("rue principale nord-est")
        assert r["thoroughfare_leading_type"] == "RUE"
        assert r["thoroughfare_name"] == "PRINCIPALE"
        assert r["thoroughfare_post_direction"] == "NE"


class TestFallback:
    def test_unrecognised_road_goes_to_thoroughfare_name(self) -> None:
        r = split_road("cul-de-sac des érables")
        assert r["thoroughfare_name"] == "CUL-DE-SAC DES ÉRABLES"
        assert "thoroughfare_leading_type" not in r
        assert "thoroughfare_trailing_type" not in r

    def test_empty_string_returns_empty_dict(self) -> None:
        assert split_road("") == {}

    def test_single_token_is_thoroughfare_name(self) -> None:
        r = split_road("broadway")
        assert r["thoroughfare_name"] == "BROADWAY"
