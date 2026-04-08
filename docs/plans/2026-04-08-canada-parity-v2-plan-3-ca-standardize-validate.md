# Canada Parity v2 — Plan 3: CA Standardization + Full Pipeline Parity

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Canada Post standardization (province normalization, postal code formatting, bilingual suffix table), wire the CA standardize endpoint in v2, and enable raw-string input through the full CA pipeline on `/api/v2/validate`. At the end of this plan, Canada has full parity with US across parse, standardize, and validate.

**Architecture:** `canada_post_data/` gets its province and suffix tables. `standardizer.py` gains `_standardize_ca()` which dispatches on country. v2 standardize and validate routers support CA. The `standardized` single-line string uses the existing i18naddress template for `CA`.

**Tech Stack:** Python 3.12+, FastAPI, `google-i18n-address` (already a dep), `core/address_format.py` (existing).

**Prerequisite:** Plans 1 and 2 must be merged.

---

## File Map

**Create:**
- `src/address_validator/canada_post_data/provinces.py`
- `src/address_validator/canada_post_data/suffixes.py`
- `src/address_validator/canada_post_data/spec.py`
- `tests/unit/test_canada_post_data.py`
- `tests/integration/test_v2_standardize_ca.py`
- `tests/integration/test_v2_validate_ca.py`

**Modify:**
- `src/address_validator/services/standardizer.py` — add `_standardize_ca()` + country dispatch
- `src/address_validator/routers/v2/standardize.py` — enable CA via `check_country_v2`
- `src/address_validator/routers/v2/validate.py` — full CA pipeline (parse → standardize → Google)

---

## Task 1: canada_post_data province and suffix tables

**Files:**
- Create: `src/address_validator/canada_post_data/provinces.py`
- Create: `src/address_validator/canada_post_data/suffixes.py`
- Create: `src/address_validator/canada_post_data/spec.py`
- Test: `tests/unit/test_canada_post_data.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_canada_post_data.py
"""Tests for Canada Post data tables."""
import pytest
from address_validator.canada_post_data.provinces import PROVINCE_MAP
from address_validator.canada_post_data.suffixes import CA_SUFFIX_MAP
from address_validator.canada_post_data.spec import CANADA_POST_SPEC, CANADA_POST_SPEC_VERSION


class TestProvinceMap:
    def test_has_13_entries(self) -> None:
        assert len(PROVINCE_MAP) == 13

    def test_all_values_are_2_char_uppercase(self) -> None:
        for abbr in PROVINCE_MAP.values():
            assert len(abbr) == 2
            assert abbr == abbr.upper()

    def test_lookup_by_full_name_case_insensitive(self) -> None:
        assert PROVINCE_MAP.get("ONTARIO") == "ON"
        assert PROVINCE_MAP.get("BRITISH COLUMBIA") == "BC"
        assert PROVINCE_MAP.get("QUEBEC") == "QC"

    def test_abbreviation_maps_to_itself(self) -> None:
        # Abbreviations should round-trip: ON → ON
        assert PROVINCE_MAP.get("ON") == "ON"
        assert PROVINCE_MAP.get("BC") == "BC"
        assert PROVINCE_MAP.get("QC") == "QC"

    def test_all_13_provinces_and_territories_present(self) -> None:
        expected_abbrs = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
        assert expected_abbrs <= set(PROVINCE_MAP.values())


class TestSuffixMap:
    def test_common_english_suffixes_present(self) -> None:
        assert CA_SUFFIX_MAP.get("STREET") == "ST"
        assert CA_SUFFIX_MAP.get("AVENUE") == "AVE"
        assert CA_SUFFIX_MAP.get("BOULEVARD") == "BLVD"
        assert CA_SUFFIX_MAP.get("DRIVE") == "DR"
        assert CA_SUFFIX_MAP.get("ROAD") == "RD"
        assert CA_SUFFIX_MAP.get("CRESCENT") == "CRES"

    def test_french_suffixes_present(self) -> None:
        assert CA_SUFFIX_MAP.get("RUE") == "RUE"
        assert CA_SUFFIX_MAP.get("BOULEVARD") == "BLVD"
        assert CA_SUFFIX_MAP.get("CHEMIN") == "CH"


class TestSpec:
    def test_spec_constants(self) -> None:
        assert CANADA_POST_SPEC == "canada-post"
        assert CANADA_POST_SPEC_VERSION == "2025"
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/unit/test_canada_post_data.py -v --no-cov
```

Expected: `ImportError`.

- [ ] **Step 3: Create provinces.py**

```python
# src/address_validator/canada_post_data/provinces.py
"""Canada Post province and territory lookup table.

Maps both full names (uppercase) and 2-letter abbreviations to the
official Canada Post 2-letter abbreviation.  All lookups must be
performed on uppercased input.

Source: Canada Post Addressing Guidelines, Table 1.
"""

# Keys: uppercase full name or abbreviation.  Values: 2-letter abbreviation.
PROVINCE_MAP: dict[str, str] = {
    # Abbreviation → abbreviation (identity; for normalising already-abbreviated input)
    "AB": "AB", "BC": "BC", "MB": "MB", "NB": "NB", "NL": "NL",
    "NS": "NS", "NT": "NT", "NU": "NU", "ON": "ON", "PE": "PE",
    "QC": "QC", "SK": "SK", "YT": "YT",
    # Full name → abbreviation
    "ALBERTA":                    "AB",
    "BRITISH COLUMBIA":           "BC",
    "MANITOBA":                   "MB",
    "NEW BRUNSWICK":              "NB",
    "NEWFOUNDLAND AND LABRADOR":  "NL",
    "NEWFOUNDLAND":               "NL",
    "LABRADOR":                   "NL",
    "NOVA SCOTIA":                "NS",
    "NORTHWEST TERRITORIES":      "NT",
    "NUNAVUT":                    "NU",
    "ONTARIO":                    "ON",
    "PRINCE EDWARD ISLAND":       "PE",
    "QUEBEC":                     "QC",
    "QUÉBEC":                     "QC",
    "SASKATCHEWAN":               "SK",
    "YUKON":                      "YT",
    "YUKON TERRITORY":            "YT",
}
```

- [ ] **Step 4: Create suffixes.py**

```python
# src/address_validator/canada_post_data/suffixes.py
"""Canada Post street type (suffix) lookup table.

Maps full-form and common abbreviations to the Canada Post standard
abbreviation.  All lookups must be performed on uppercased input.

Source: Canada Post Addressing Guidelines, Table 3 (English) and
the bilingual equivalent for French types.
"""

CA_SUFFIX_MAP: dict[str, str] = {
    # English street types
    "ALLEY":         "ALLEY",
    "AVE":           "AVE",    "AVENUE":     "AVE",
    "BAY":           "BAY",
    "BEACH":         "BEACH",
    "BEND":          "BEND",
    "BLVD":          "BLVD",   "BOULEVARD":  "BLVD",
    "BYPASS":        "BYPASS",
    "CAMPUS":        "CAMPUS",
    "CAPE":          "CAPE",
    "CENTRE":        "CTR",    "CENTER":     "CTR",    "CTR": "CTR",
    "CHASE":         "CHASE",
    "CIRCLE":        "CIRC",   "CIRC":       "CIRC",
    "CIRCUIT":       "CIRCT",  "CIRCT":      "CIRCT",
    "CLOSE":         "CLOSE",
    "COMMON":        "COMMON",
    "CONCESSION":    "CONC",   "CONC":       "CONC",
    "CORNERS":       "CRNRS",  "CRNRS":      "CRNRS",
    "COURT":         "CRT",    "CRT":        "CRT",
    "COVE":          "COVE",
    "CRESCENT":      "CRES",   "CRES":       "CRES",
    "CROSSING":      "CROSS",  "CROSS":      "CROSS",
    "CUL-DE-SAC":    "CDS",    "CDS":        "CDS",
    "DALE":          "DALE",
    "DELL":          "DELL",
    "DIVERSION":     "DIVERS", "DIVERS":     "DIVERS",
    "DOWNS":         "DOWNS",
    "DR":            "DR",     "DRIVE":      "DR",
    "END":           "END",
    "ESPLANADE":     "ESPL",   "ESPL":       "ESPL",
    "ESTATES":       "ESTATE", "ESTATE":     "ESTATE",
    "EXPRESSWAY":    "EXPY",   "EXPY":       "EXPY",
    "EXTENSION":     "EXTEN",  "EXTEN":      "EXTEN",
    "FARM":          "FARM",
    "FIELD":         "FIELD",
    "FOREST":        "FOREST",
    "FREEWAY":       "FWY",    "FWY":        "FWY",
    "FRONT":         "FRONT",
    "GARDENS":       "GDNS",   "GDNS":       "GDNS",
    "GATE":          "GATE",
    "GLADE":         "GLADE",
    "GLEN":          "GLEN",
    "GREEN":         "GREEN",
    "GROUNDS":       "GRNDS",  "GRNDS":      "GRNDS",
    "GROVE":         "GROVE",
    "HARBOUR":       "HARBR",  "HARBR":      "HARBR",
    "HEATH":         "HEATH",
    "HEIGHTS":       "HTS",    "HTS":        "HTS",
    "HIGHLANDS":     "HGHLDS", "HGHLDS":     "HGHLDS",
    "HIGHWAY":       "HWY",    "HWY":        "HWY",
    "HILL":          "HILL",
    "HOLLOW":        "HOLLOW",
    "INLET":         "INLET",
    "ISLAND":        "ISLAND",
    "KEY":           "KEY",
    "KNOLL":         "KNOLL",
    "LANDING":       "LANDNG", "LANDNG":     "LANDNG",
    "LANE":          "LANE",   "LN":         "LANE",
    "LIMITS":        "LMTS",   "LMTS":       "LMTS",
    "LINE":          "LINE",
    "LINK":          "LINK",
    "LOOKOUT":       "LKOUT",  "LKOUT":      "LKOUT",
    "LOOP":          "LOOP",
    "MALL":          "MALL",
    "MANOR":         "MANOR",
    "MAZE":          "MAZE",
    "MEADOW":        "MEADOW",
    "MEWS":          "MEWS",
    "MOOR":          "MOOR",
    "MOUNT":         "MOUNT",
    "MOUNTAIN":      "MTN",    "MTN":        "MTN",
    "ORCHARD":       "ORCH",   "ORCH":       "ORCH",
    "PARADE":        "PARADE",
    "PARK":          "PARK",   "PK":         "PARK",
    "PARKWAY":       "PKY",    "PKY":        "PKY",
    "PATH":          "PATH",
    "PATHWAY":       "PTWAY",  "PTWAY":      "PTWAY",
    "PL":            "PL",     "PLACE":      "PL",
    "PLATEAU":       "PLAT",   "PLAT":       "PLAT",
    "PLAZA":         "PLAZA",
    "POINT":         "PT",     "PT":         "PT",
    "PORT":          "PORT",
    "PRIVATE":       "PVT",    "PVT":        "PVT",
    "PROM":          "PROM",   "PROMENADE":  "PROM",
    "QUAI":          "QUAI",   "QUAY":       "QUAY",
    "RAMP":          "RAMP",
    "RD":            "RD",     "ROAD":       "RD",
    "RIDGE":         "RIDGE",
    "RISE":          "RISE",
    "RUN":           "RUN",
    "ROW":           "ROW",
    "RTE":           "RTE",
    "SQUARE":        "SQ",     "SQ":         "SQ",
    "ST":            "ST",     "STREET":     "ST",
    "SUBDIVISION":   "SUBDIV", "SUBDIV":     "SUBDIV",
    "TERRACE":       "TERR",   "TERR":       "TERR",
    "THICKET":       "THICK",  "THICK":      "THICK",
    "TOWERS":        "TOWERS",
    "TOWNLINE":      "TLINE",  "TLINE":      "TLINE",
    "TRAIL":         "TRAIL",
    "TURNING":       "TRNING", "TRNING":     "TRNING",
    "VALE":          "VALE",
    "VIA":           "VIA",
    "VIEW":          "VIEW",
    "VILLAGE":       "VILLGE", "VILLGE":     "VILLGE",
    "VILLAS":        "VILLAS",
    "VISTA":         "VISTA",
    "WAY":           "WAY",
    "WOOD":          "WOOD",
    "WYND":          "WYND",
    # French street types (bilingual Canada Post standard)
    "ALLÉE":         "ALLÉE",  "ALLEE":      "ALLÉE",
    "AV":            "AVE",
    "CH":            "CH",     "CHEMIN":     "CH",
    "CROIS":         "CROIS",  "CROISSANT":  "CROIS",
    "CÔTE":          "CÔTE",   "COTE":       "CÔTE",
    "IMP":           "IMP",    "IMPASSE":    "IMP",
    "MONTÉE":        "MONTÉE", "MONTEE":     "MONTÉE",
    "PASS":          "PASS",   "PASSAGE":    "PASS",
    "RANG":          "RANG",
    "ROUT":          "ROUT",   "ROUTE":      "ROUT",
    "RUE":           "RUE",
    "RUELLE":        "RUELLE",
    "SENT":          "SENT",   "SENTIER":    "SENT",
    "TRAV":          "TRAV",   "TRAVERSE":   "TRAV",
    "VOIE":          "VOIE",
}
```

- [ ] **Step 5: Create spec.py**

```python
# src/address_validator/canada_post_data/spec.py
"""Canada Post specification identifiers for ComponentSet."""

CANADA_POST_SPEC: str = "canada-post"
CANADA_POST_SPEC_VERSION: str = "2025"
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_canada_post_data.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/address_validator/canada_post_data/ tests/unit/test_canada_post_data.py --fix
uv run ruff format src/address_validator/canada_post_data/ tests/unit/test_canada_post_data.py
git add src/address_validator/canada_post_data/ tests/unit/test_canada_post_data.py
git commit -m "#90 feat: add Canada Post province, suffix, and spec tables"
```

---

## Task 2: standardizer.py — add _standardize_ca()

**Files:**
- Modify: `src/address_validator/services/standardizer.py`
- Modify: `tests/unit/test_standardizer.py`

- [ ] **Step 1: Write failing tests for CA standardization**

Add to `tests/unit/test_standardizer.py`:

```python
class TestStandardizeCA:
    def test_province_abbreviation_normalised(self) -> None:
        from address_validator.services.standardizer import standardize
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ONTARIO",   # full name
            "postcode": "M5V 2T6",
        }
        result = standardize(comps, country="CA")
        assert result.components.values["administrative_area"] == "ON"
        assert result.region == "ON"

    def test_postal_code_uppercase_and_spaced(self) -> None:
        from address_validator.services.standardizer import standardize
        comps = {
            "premise_number": "100",
            "thoroughfare_name": "OAK",
            "thoroughfare_trailing_type": "AVE",
            "locality": "VANCOUVER",
            "administrative_area": "BC",
            "postcode": "v5k0a1",   # lowercase, no space
        }
        result = standardize(comps, country="CA")
        assert result.components.values["postcode"] == "V5K 0A1"
        assert result.postal_code == "V5K 0A1"

    def test_suffix_normalised(self) -> None:
        from address_validator.services.standardizer import standardize
        comps = {
            "premise_number": "200",
            "thoroughfare_name": "ELM",
            "thoroughfare_trailing_type": "STREET",   # full → ST
            "locality": "OTTAWA",
            "administrative_area": "ON",
            "postcode": "K1A 0A6",
        }
        result = standardize(comps, country="CA")
        assert result.components.values["thoroughfare_trailing_type"] == "ST"

    def test_spec_is_canada_post(self) -> None:
        from address_validator.services.standardizer import standardize
        comps = {
            "premise_number": "1",
            "thoroughfare_name": "TEST",
            "locality": "MONTREAL",
            "administrative_area": "QC",
            "postcode": "H3A 1A1",
        }
        result = standardize(comps, country="CA")
        assert result.components.spec == "canada-post"
        assert result.components.spec_version == "2025"

    def test_standardized_string_built(self) -> None:
        from address_validator.services.standardizer import standardize
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        result = standardize(comps, country="CA")
        assert result.standardized  # non-empty
        assert "TORONTO" in result.standardized
        assert "ON" in result.standardized
        assert "M5V 2T6" in result.standardized
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/unit/test_standardizer.py::TestStandardizeCA -v --no-cov
```

Expected: `FAILED` — `standardize()` does not yet handle `country="CA"`.

- [ ] **Step 3: Add _standardize_ca() to standardizer.py**

Read `src/address_validator/services/standardizer.py` in full before editing. Then add:

```python
# At the top, add imports:
from address_validator.canada_post_data.provinces import PROVINCE_MAP
from address_validator.canada_post_data.suffixes import CA_SUFFIX_MAP
from address_validator.canada_post_data.spec import CANADA_POST_SPEC, CANADA_POST_SPEC_VERSION
import re as _re

# Add this function before standardize():

def _std_postal_code_ca(raw: str) -> str:
    """Normalise a Canadian postal code to ``A1A 1A1`` format.

    Strips whitespace, uppercases, and inserts the required space after
    the FSA (first three characters).  Returns the raw value unchanged
    if it does not match the expected six-character pattern after cleaning.
    """
    cleaned = raw.upper().replace(" ", "").replace("-", "")
    if _re.fullmatch(r"[A-Z]\d[A-Z]\d[A-Z]\d", cleaned):
        return f"{cleaned[:3]} {cleaned[3:]}"
    return raw.upper()


def _standardize_ca(
    components: dict[str, str],
    upstream_warnings: list[str],
) -> StandardizeResponseV1:
    """Standardise a Canadian address per Canada Post Addressing Guidelines.

    Normalises:
    - ``administrative_area``: full province name → 2-letter abbreviation
    - ``postcode``: uppercase + FSA-space-LDU format
    - ``thoroughfare_trailing_type`` / ``thoroughfare_leading_type``: CA suffix table
    - ``thoroughfare_pre_direction`` / ``thoroughfare_post_direction``: CA directionals

    Components not present in the input are omitted from the output.
    """
    from address_validator.canada_post_data.directionals import CA_DIRECTIONAL_MAP

    std: dict[str, str] = {}
    warnings: list[str] = list(upstream_warnings)

    # Copy all components as-is first; normalise known fields below.
    for k, v in components.items():
        if v:
            std[k] = v

    # --- administrative_area (province) ---
    region = _get(components, "administrative_area")
    if region:
        abbr = PROVINCE_MAP.get(region.upper())
        if abbr:
            std["administrative_area"] = abbr
        else:
            warnings.append(f"Unrecognised province/territory: '{region}'")
            std["administrative_area"] = region.upper()

    # --- postcode ---
    postcode = _get(components, "postcode")
    if postcode:
        std["postcode"] = _std_postal_code_ca(postcode)

    # --- thoroughfare types ---
    for key, table in (
        ("thoroughfare_trailing_type", CA_SUFFIX_MAP),
        ("thoroughfare_leading_type", CA_SUFFIX_MAP),
    ):
        v = _get(components, key)
        if v:
            std[key] = table.get(v.upper(), v.upper())

    # --- directionals ---
    for key in ("thoroughfare_pre_direction", "thoroughfare_post_direction"):
        v = _get(components, key)
        if v:
            std[key] = CA_DIRECTIONAL_MAP.get(v.lower(), v.upper())

    # --- Build top-level response fields ---
    locality = std.get("locality", "")
    admin_area = std.get("administrative_area", "")
    postcode_out = std.get("postcode", "")

    # Build address lines for the standardized string.
    premise = std.get("premise_number", "")
    pre_dir = std.get("thoroughfare_pre_direction", "")
    leading_type = std.get("thoroughfare_leading_type", "")
    name = std.get("thoroughfare_name", "")
    trailing_type = std.get("thoroughfare_trailing_type", "")
    post_dir = std.get("thoroughfare_post_direction", "")
    unit_type = std.get("sub_premise_type", "")
    unit_id = std.get("sub_premise_number", "")

    # address_line_1: number + street
    street_parts = [p for p in (pre_dir, leading_type, name, trailing_type, post_dir) if p]
    street = " ".join(street_parts)
    unit_part = " ".join(p for p in (unit_type, unit_id) if p)
    address_line_1 = " ".join(p for p in (premise, street) if p)
    address_line_2 = unit_part

    standardized = build_validated_string(
        address_line_1, address_line_2, locality, admin_area, postcode_out
    )

    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=locality,
        region=admin_area,
        postal_code=postcode_out,
        country="CA",
        standardized=standardized,
        components=ComponentSet(
            spec=CANADA_POST_SPEC,
            spec_version=CANADA_POST_SPEC_VERSION,
            values=std,
        ),
        warnings=warnings,
    )
```

- [ ] **Step 4: Update standardize() to dispatch on country**

Read the current `standardize()` function signature. Add a country dispatch at the top:

```python
def standardize(
    components: dict[str, str],
    country: str = "US",
    upstream_warnings: list[str] | None = None,
) -> StandardizeResponseV1:
    """Standardise components per national postal profile.

    Dispatches to ``_standardize_ca()`` for ``country="CA"`` and the
    existing USPS Pub 28 pipeline for ``country="US"`` (default).
    """
    warnings = list(upstream_warnings or [])
    if country == "CA":
        return _standardize_ca(components, warnings)
    # ... existing US logic follows unchanged ...
```

Read the existing `standardize()` to find the exact signature and adjust — it may already accept `upstream_warnings`. Do not duplicate the US logic; add the CA branch at the top and fall through.

- [ ] **Step 5: Run CA standardize tests**

```bash
uv run pytest tests/unit/test_standardizer.py -v --no-cov
```

Expected: all tests pass, including the new `TestStandardizeCA` class.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/address_validator/services/standardizer.py tests/unit/test_standardizer.py --fix
uv run ruff format src/address_validator/services/standardizer.py tests/unit/test_standardizer.py
git add src/address_validator/services/standardizer.py tests/unit/test_standardizer.py
git commit -m "#90 feat: add _standardize_ca() with Canada Post province, postal code, suffix normalisation"
```

---

## Task 3: v2 standardize router — enable CA

**Files:**
- Modify: `src/address_validator/routers/v2/standardize.py`
- Create: `tests/integration/test_v2_standardize_ca.py`

- [ ] **Step 1: Write failing integration tests**

```python
# tests/integration/test_v2_standardize_ca.py
"""Integration tests for POST /api/v2/standardize with country=CA."""
from unittest.mock import AsyncMock, patch


class TestV2StandardizeCA:
    def test_ca_address_returns_canada_post_spec(self, client) -> None:
        mock_parse = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "STREET",
            "locality": "TORONTO",
            "administrative_area": "ONTARIO",
            "postcode": "m5v2t6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_parse,
        ):
            response = client.post(
                "/api/v2/standardize",
                json={
                    "address": "123 Main Street Toronto Ontario M5V 2T6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["components"]["spec"] == "canada-post"
        assert body["components"]["spec_version"] == "2025"
        values = body["components"]["values"]
        assert values["administrative_area"] == "ON"
        assert values["postcode"] == "M5V 2T6"
        assert values["thoroughfare_trailing_type"] == "ST"
        assert body["region"] == "ON"
        assert body["postal_code"] == "M5V 2T6"

    def test_ca_not_available_in_v1_standardize(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_ca_standardize_with_components_input(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={
                "country": "CA",
                "components": {
                    "premise_number": "100",
                    "thoroughfare_name": "OAK",
                    "thoroughfare_trailing_type": "AVENUE",
                    "locality": "VANCOUVER",
                    "administrative_area": "BC",
                    "postcode": "v5k0a1",
                },
            },
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["administrative_area"] == "BC"
        assert values["postcode"] == "V5K 0A1"
        assert values["thoroughfare_trailing_type"] == "AVE"

    def test_french_ca_address_standardized(self, client) -> None:
        mock_parse = {
            "premise_number": "350",
            "thoroughfare_leading_type": "RUE",
            "thoroughfare_name": "DES LILAS",
            "thoroughfare_post_direction": "O",
            "locality": "QUEBEC",
            "administrative_area": "QC",
            "postcode": "g1l1b6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_parse,
        ):
            response = client.post(
                "/api/v2/standardize",
                json={
                    "address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["thoroughfare_leading_type"] == "RUE"
        assert values["postcode"] == "G1L 1B6"
        assert values["administrative_area"] == "QC"
```

- [ ] **Step 2: Update v2 standardize.py to use check_country_v2**

In `src/address_validator/routers/v2/standardize.py`, change:

```python
# Before:
from address_validator.routers.v1.core import APIError, check_country

# After:
from address_validator.routers.v1.core import APIError, check_country_v2
```

And in the handler:
```python
# Before:
check_country(req.country)

# After:
check_country_v2(req.country)
```

Also pass `libpostal_client` to `parse_address()` when it is called (for CA raw string input):

```python
libpostal_client = getattr(request.app.state, "libpostal_client", None)
# ... in the address branch:
parse_result = await parse_address(req.address.strip(), country=req.country,
                                    libpostal_client=libpostal_client)
```

Handle `LibpostalUnavailableError` with a 503 (same pattern as v2 parse router).

- [ ] **Step 3: Run CA standardize tests**

```bash
uv run pytest tests/integration/test_v2_standardize_ca.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 4: Run full suite**

```bash
uv run pytest --no-cov -x
```

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v2/standardize.py tests/integration/test_v2_standardize_ca.py --fix
uv run ruff format src/address_validator/routers/v2/standardize.py tests/integration/test_v2_standardize_ca.py
git add src/address_validator/routers/v2/standardize.py tests/integration/test_v2_standardize_ca.py
git commit -m "#90 feat: enable CA in v2 standardize endpoint"
```

---

## Task 4: v2 validate router — full CA pipeline

**Files:**
- Modify: `src/address_validator/routers/v2/validate.py`
- Create: `tests/integration/test_v2_validate_ca.py`

- [ ] **Step 1: Write failing integration tests**

```python
# tests/integration/test_v2_validate_ca.py
"""Integration tests for POST /api/v2/validate with country=CA."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestV2ValidateCA:
    def test_ca_raw_string_accepted_in_v2(self, client) -> None:
        """v2 validate accepts a raw CA address string (unlike v1 which requires components)."""
        mock_parse = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_parse,
        ):
            response = client.post(
                "/api/v2/validate",
                json={
                    "address": "123 Main St Toronto ON M5V 2T6",
                    "country": "CA",
                },
            )
        # Without Google provider configured, status is "unavailable" — still 200
        assert response.status_code == 200
        assert response.json()["api_version"] == "2"

    def test_ca_not_available_in_v1_validate_with_raw_string(self, client) -> None:
        response = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_ca_components_input_accepted_in_v2(self, client) -> None:
        response = client.post(
            "/api/v2/validate",
            json={
                "country": "CA",
                "components": {
                    "address_line_1": "123 MAIN ST",
                    "city": "TORONTO",
                    "region": "ON",
                    "postal_code": "M5V 2T6",
                },
            },
        )
        assert response.status_code == 200
```

- [ ] **Step 2: Update v2 validate.py**

Read `src/address_validator/routers/v2/validate.py` in full. The key changes from v1 are:

1. Use `check_country_v2` instead of `check_country` for non-components paths.
2. CA raw string input must be supported (v1 blocked non-US raw strings; v2 allows them if the CA parse pipeline is available).
3. Pass `libpostal_client` to `parse_address()` for CA.
4. Handle `LibpostalUnavailableError` → 503.

In the validate handler, the relevant block that currently rejects non-US raw strings:

```python
# v1 pattern (rejects non-US raw strings):
if req.country != "US":
    if not req.components:
        raise APIError(status_code=422, error="country_not_supported", ...)
```

In v2, replace with:

```python
# v2 pattern: CA raw strings are supported via libpostal
if req.country not in ("US", "CA"):
    if not req.components:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Raw address strings are only supported for US and CA. "
                "Supply pre-parsed components for other countries."
            ),
        )
```

And for the CA parse call in the main path:

```python
libpostal_client = getattr(request.app.state, "libpostal_client", None)
try:
    parse_result = await parse_address(
        req.address.strip(), country=req.country, libpostal_client=libpostal_client
    )
except LibpostalUnavailableError:
    raise APIError(
        status_code=503,
        error="parsing_unavailable",
        message="CA address parsing is currently unavailable. Provide pre-parsed components.",
    )
comps = parse_result.components.values
```

- [ ] **Step 3: Run CA validate tests**

```bash
uv run pytest tests/integration/test_v2_validate_ca.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 4: Run full test suite and coverage**

```bash
uv run pytest --no-cov -x
uv run pytest --cov --cov-report=term-missing
```

Expected: all pass; coverage ≥ 80%.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v2/validate.py tests/integration/test_v2_validate_ca.py --fix
uv run ruff format src/address_validator/routers/v2/validate.py tests/integration/test_v2_validate_ca.py
git add src/address_validator/routers/v2/validate.py tests/integration/test_v2_validate_ca.py
git commit -m "#90 feat: enable full CA pipeline in v2 validate endpoint"
```

---

## Task 5: Final integration and smoke tests

- [ ] **Step 1: Run complete test suite**

```bash
uv run pytest --no-cov
```

Expected: all pass, zero failures.

- [ ] **Step 2: Coverage**

```bash
uv run pytest --cov --cov-report=term-missing
```

Expected: ≥ 80% line and branch coverage. If below, identify the uncovered CA paths and add targeted tests.

- [ ] **Step 3: Full lint pass**

```bash
uv run ruff check . --fix
uv run ruff format .
```

Expected: no issues.

- [ ] **Step 4: End-to-end smoke test against dev server**

With libpostal sidecar running and the dev server started (`uvicorn address_validator.main:app --port 8001 --reload`):

```bash
# Full CA pipeline: parse → standardize
curl -s -X POST http://localhost:8001/api/v2/standardize \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6", "country": "CA"}' \
  | jq '{spec: .components.spec, region: .region, postal_code: .postal_code, standardized: .standardized}'

# Expected:
# {
#   "spec": "canada-post",
#   "region": "QC",
#   "postal_code": "G1L 1B6",
#   "standardized": "350 RUE DES LILAS O  QUÉBEC QC  G1L 1B6"
# }

# v1 still works for US
curl -s -X POST http://localhost:8001/api/v1/standardize \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 n main st ste 4, seattle wa 98101"}' \
  | jq '.components.values | keys'
# Expected: ["address_number", "occupancy_identifier", "occupancy_type", ...]

# v2 US with ISO keys
curl -s -X POST http://localhost:8001/api/v2/standardize \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 N Main St Ste 4, Seattle WA 98101"}' \
  | jq '.components.values | keys'
# Expected: ["locality", "postcode", "premise_number", "sub_premise_number", ...]
```

- [ ] **Step 5: Commit final state**

```bash
git add -A
git commit -m "#90 chore: final lint and smoke test pass — CA full parity complete"
```

---

## What Full Parity Looks Like

After Plan 3, Canadian addresses have the same capabilities as US:

| Endpoint | US | CA (v2) |
|---|---|---|
| `/api/v2/parse` | ✓ raw string | ✓ raw string via libpostal |
| `/api/v2/standardize` | ✓ raw string + components | ✓ raw string + components |
| `/api/v2/validate` | ✓ raw string + components | ✓ raw string + components |
| `/api/v2/countries/CA/format` | — | ✓ (was already working) |
| `/api/v1/*` | ✓ unchanged | ✗ (v1 = US-only) |

Component spec: `usps-pub28` for US, `canada-post` for CA.
Default component key vocabulary in v2: ISO 19160-4 for both.
