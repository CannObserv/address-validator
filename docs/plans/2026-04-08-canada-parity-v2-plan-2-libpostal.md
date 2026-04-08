# Canada Parity v2 — Plan 2: libpostal Sidecar + CA Parsing

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the libpostal Docker sidecar, implement `LibpostalClient` and the bilingual street component splitter, and wire Canadian address parsing into `/api/v2/parse`.

**Architecture:** The libpostal sidecar (`pelias/libpostal-service`) runs as a systemd-managed Docker container on port 4400. `LibpostalClient` (async httpx) calls it and translates libpostal tags to ISO 19160-4 keys via the bilingual street splitter. `parse_address()` in `parser.py` dispatches to the libpostal path when `country="CA"`. Canada is added to `SUPPORTED_COUNTRIES` for v2. v1 parse remains US-only.

**Tech Stack:** `pelias/libpostal-service` Docker image, `httpx` (already a dep), systemd, Python 3.12+.

**Prerequisite:** Plan 1 must be merged. All internal keys are ISO 19160-4. v2 API surface exists.

---

## File Map

**Create:**
- `libpostal.service` (repo root — copied to `/etc/systemd/system/` at deploy)
- `src/address_validator/services/libpostal_client.py`
- `src/address_validator/services/street_splitter.py`
- `src/address_validator/canada_post_data/__init__.py`
- `src/address_validator/canada_post_data/directionals.py`
- `tests/unit/test_street_splitter.py`
- `tests/unit/test_libpostal_client.py`
- `tests/integration/test_v2_parse_ca.py`

**Modify:**
- `src/address_validator/services/parser.py` — add `parse_address_ca()` + country dispatch
- `src/address_validator/routers/v1/core.py` — add v2-scoped `SUPPORTED_COUNTRIES_V2`
- `src/address_validator/routers/v2/parse.py` — use v2 supported countries; pass libpostal client
- `src/address_validator/main.py` — `LibpostalClient` lifespan; `app.state.libpostal_client`

---

## Task 1: libpostal.service systemd unit

**Files:**
- Create: `libpostal.service` (repo root)

No test — operational artifact.

- [ ] **Step 1: Create libpostal.service**

```ini
# libpostal.service
[Unit]
Description=libpostal address parsing service (pelias/libpostal-service)
Documentation=https://github.com/pelias/libpostal-service
After=docker.service
Requires=docker.service

[Service]
Restart=always
RestartSec=5

# Remove any stale container from a previous run before starting.
ExecStartPre=-/usr/bin/docker stop libpostal
ExecStartPre=-/usr/bin/docker rm libpostal

ExecStart=/usr/bin/docker run \
    --rm \
    --name libpostal \
    -p 127.0.0.1:4400:4400 \
    --memory=2.5g \
    pelias/libpostal-service

ExecStop=/usr/bin/docker stop libpostal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Pull image and verify it starts**

```bash
docker pull pelias/libpostal-service
docker run --rm -p 127.0.0.1:4400:4400 --memory=2.5g pelias/libpostal-service &
sleep 30   # libpostal model load takes 10–30s
curl -s "http://localhost:4400/parse?address=123+Main+St+Seattle+WA+98101" | jq .
```

Expected JSON array: `[{"label":"house_number","value":"123"}, {"label":"road","value":"main st"}, ...]`

Kill the test container when confirmed:
```bash
docker stop $(docker ps -q --filter name=libpostal) 2>/dev/null || true
```

- [ ] **Step 3: Install and start the service**

```bash
sudo cp libpostal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now libpostal.service
sudo systemctl status libpostal.service
```

Wait ~30 seconds for the model to load, then verify:
```bash
curl -s "http://localhost:4400/parse?address=350+rue+des+Lilas+Ouest+Quebec+QC+G1L+1B6" | jq .
```

Expected: parsed components including `house_number`, `road`, `city`, `state`, `postcode`.

- [ ] **Step 4: Add address-validator.service dependency**

Add these lines to `address-validator.service` (after the existing `After=` line):

```ini
After=libpostal.service postgresql.service
Wants=libpostal.service
```

`Wants=` (not `Requires=`) — address-validator starts even if libpostal is down. CA parse returns 503; US parse is unaffected.

```bash
sudo cp address-validator.service /etc/systemd/system/
sudo systemctl daemon-reload
```

- [ ] **Step 5: Commit**

```bash
git add libpostal.service address-validator.service
git commit -m "#90 chore: add libpostal.service systemd unit and address-validator dependency"
```

---

## Task 2: canada_post_data/directionals.py

**Files:**
- Create: `src/address_validator/canada_post_data/__init__.py`
- Create: `src/address_validator/canada_post_data/directionals.py`

This directional table is needed by the street splitter in Task 3. The bilingual table covers English and French directionals found in Canadian addresses.

- [ ] **Step 1: Create canada_post_data/__init__.py**

```python
# src/address_validator/canada_post_data/__init__.py
```

(Empty.)

- [ ] **Step 2: Create directionals.py**

```python
# src/address_validator/canada_post_data/directionals.py
"""Bilingual directional lookup for Canadian addresses.

Maps normalised directional tokens (lowercase, no punctuation) to the
Canada Post abbreviated form.  Covers English and French directionals
including compound forms.

Source: Canada Post Addressing Guidelines §3.
"""

CA_DIRECTIONAL_MAP: dict[str, str] = {
    # English — single
    "north": "N",  "n": "N",
    "south": "S",  "s": "S",
    "east":  "E",  "e": "E",
    "west":  "W",  "w": "W",
    # English — compound
    "northeast": "NE", "ne": "NE",
    "northwest": "NW", "nw": "NW",
    "southeast": "SE", "se": "SE",
    "southwest": "SW", "sw": "SW",
    # French — single
    "nord":  "N",
    "sud":   "S",
    "est":   "E",
    "ouest": "O",   # Canada Post uses O for Ouest
    # French — compound
    "nord-est":   "NE", "nordest":   "NE",
    "nord-ouest": "NO", "nordouest": "NO",
    "sud-est":    "SE", "sudest":    "SE",
    "sud-ouest":  "SO", "sudouest":  "SO",
}
```

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/canada_post_data/
git commit -m "#90 feat: add canada_post_data package with bilingual directional table"
```

---

## Task 3: street_splitter.py — bilingual street component splitter

**Files:**
- Create: `src/address_validator/services/street_splitter.py`
- Test: `tests/unit/test_street_splitter.py`

Decomposes libpostal's composite `road` token into ISO 19160-4 thoroughfare elements. Handles English trailing types, French leading types, bilingual directionals, and French articles.

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_street_splitter.py
"""Tests for the bilingual street component splitter."""
import pytest
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
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/unit/test_street_splitter.py -v --no-cov
```

Expected: `ImportError` — module does not exist.

- [ ] **Step 3: Create street_splitter.py**

```python
# src/address_validator/services/street_splitter.py
"""Bilingual street component splitter for Canadian addresses.

Decomposes libpostal's composite ``road`` token into ISO 19160-4
thoroughfare elements.  Handles English trailing-type addresses
(``Main St``) and French leading-type addresses (``rue des Lilas``)
as well as bilingual directionals.

Algorithm (left-to-right, position-aware):
  1. Normalise to uppercase; split on whitespace.
  2. Leading type check: if first token is a known leading/either type,
     extract as ``thoroughfare_leading_type``.
  3. Trailing directional check: if last token(s) match the bilingual
     directional table, extract as ``thoroughfare_post_direction``.
  4. Trailing type check (English): if last remaining token is a known
     trailing/either type, extract as ``thoroughfare_trailing_type``.
  5. Leading directional check: if first remaining token matches the
     directional table, extract as ``thoroughfare_pre_direction``.
  6. Remainder → ``thoroughfare_name``.
  7. Fallback: on any ambiguous construction store the full value in
     ``thoroughfare_name`` without splitting.

French articles (de, des, du, de la, de l') following a leading type
are left attached to the name — they are part of the street name, not
the type.
"""

from __future__ import annotations

from address_validator.canada_post_data.directionals import CA_DIRECTIONAL_MAP

# ---------------------------------------------------------------------------
# Street type table
# ---------------------------------------------------------------------------
# Position values:
#   "leading"  — French-style type before the name  (rue, chemin)
#   "trailing" — English-style type after the name  (street, road)
#   "either"   — valid in both positions             (avenue, boulevard)
#
# Values are the normalised Canada Post abbreviation.

_STREET_TYPES: dict[str, tuple[str, str]] = {
    # token_lower: (position, abbreviation)
    # French leading
    "rue":         ("leading",  "RUE"),
    "chemin":      ("leading",  "CH"),
    "côte":        ("leading",  "CÔTE"),
    "cote":        ("leading",  "CÔTE"),
    "montée":      ("leading",  "MONTÉE"),
    "montee":      ("leading",  "MONTÉE"),
    "rang":        ("leading",  "RANG"),
    "route":       ("leading",  "ROUT"),
    "voie":        ("leading",  "VOIE"),
    "allée":       ("leading",  "ALLÉE"),
    "allee":       ("leading",  "ALLÉE"),
    "impasse":     ("leading",  "IMP"),
    "ruelle":      ("leading",  "RUELLE"),
    "sentier":     ("leading",  "SENT"),
    "traverse":    ("leading",  "TRAV"),
    # English trailing
    "street":      ("trailing", "ST"),
    "st":          ("trailing", "ST"),
    "drive":       ("trailing", "DR"),
    "dr":          ("trailing", "DR"),
    "road":        ("trailing", "RD"),
    "rd":          ("trailing", "RD"),
    "lane":        ("trailing", "LANE"),
    "ln":          ("trailing", "LANE"),
    "court":       ("trailing", "CRT"),
    "crt":         ("trailing", "CRT"),
    "crescent":    ("trailing", "CRES"),
    "cres":        ("trailing", "CRES"),
    "way":         ("trailing", "WAY"),
    "trail":       ("trailing", "TRAIL"),
    "terrace":     ("trailing", "TERR"),
    "terr":        ("trailing", "TERR"),
    "heights":     ("trailing", "HTS"),
    "hts":         ("trailing", "HTS"),
    "close":       ("trailing", "CLOSE"),
    "gate":        ("trailing", "GATE"),
    "green":       ("trailing", "GREEN"),
    "grove":       ("trailing", "GROVE"),
    "heath":       ("trailing", "HEATH"),
    "hollow":      ("trailing", "HOLLOW"),
    "mews":        ("trailing", "MEWS"),
    "park":        ("trailing", "PARK"),
    "path":        ("trailing", "PATH"),
    "rise":        ("trailing", "RISE"),
    "run":         ("trailing", "RUN"),
    "vale":        ("trailing", "VALE"),
    "view":        ("trailing", "VIEW"),
    "walk":        ("trailing", "WALK"),
    "wood":        ("trailing", "WOOD"),
    "woods":       ("trailing", "WOODS"),
    # Either position
    "avenue":      ("either",   "AVE"),
    "ave":         ("either",   "AVE"),
    "boulevard":   ("either",   "BLVD"),
    "blvd":        ("either",   "BLVD"),
    "place":       ("either",   "PL"),
    "pl":          ("either",   "PL"),
    "promenade":   ("either",   "PROM"),
    "prom":        ("either",   "PROM"),
    "quai":        ("either",   "QUAI"),
    "square":      ("either",   "SQ"),
    "sq":          ("either",   "SQ"),
    "croissant":   ("either",   "CROIS"),
    "crois":       ("either",   "CROIS"),
    "esplanade":   ("either",   "ESPL"),
    "espl":        ("either",   "ESPL"),
    "passage":     ("either",   "PASS"),
    "pass":        ("either",   "PASS"),
    "terr":        ("either",   "TERR"),
    "circle":      ("either",   "CIRC"),
    "circ":        ("either",   "CIRC"),
    "bypass":      ("either",   "BYPASS"),
    "line":        ("either",   "LINE"),
    "concession":  ("either",   "CONC"),
    "conc":        ("either",   "CONC"),
}

# French article tokens that follow a leading type and belong to the name.
_FR_ARTICLES: frozenset[str] = frozenset({"de", "des", "du", "la", "l'"})


def _normalise(token: str) -> str:
    """Uppercase, strip punctuation used for lookup but not abbreviation."""
    return token.upper()


def _lookup_directional(token: str) -> str | None:
    return CA_DIRECTIONAL_MAP.get(token.lower().replace("-", ""))


def _lookup_type(token: str) -> tuple[str, str] | None:
    return _STREET_TYPES.get(token.lower())


def split_road(road: str) -> dict[str, str]:
    """Split a libpostal ``road`` value into ISO 19160-4 thoroughfare elements.

    Returns a dict containing a subset of:
      - ``thoroughfare_leading_type``
      - ``thoroughfare_pre_direction``
      - ``thoroughfare_name``
      - ``thoroughfare_trailing_type``
      - ``thoroughfare_post_direction``

    Returns ``{}`` for empty input.  Unrecognised constructions fall back
    to storing the full value in ``thoroughfare_name``.
    """
    road = road.strip()
    if not road:
        return {}

    tokens = road.split()
    result: dict[str, str] = {}

    # --- Step 2: leading type check ---
    first = tokens[0]
    type_info = _lookup_type(first)
    if type_info and type_info[0] in ("leading", "either"):
        result["thoroughfare_leading_type"] = type_info[1]
        tokens = tokens[1:]

    # --- Step 3: trailing directional ---
    # Check for compound directionals first (two-token: "Nord Est").
    if len(tokens) >= 2:
        compound = tokens[-2].lower() + tokens[-1].lower().replace("-", "")
        dir_abbr = CA_DIRECTIONAL_MAP.get(compound)
        if dir_abbr:
            result["thoroughfare_post_direction"] = dir_abbr
            tokens = tokens[:-2]
        else:
            dir_abbr = _lookup_directional(tokens[-1])
            if dir_abbr:
                result["thoroughfare_post_direction"] = dir_abbr
                tokens = tokens[:-1]
    elif len(tokens) == 1:
        dir_abbr = _lookup_directional(tokens[0])
        if dir_abbr and "thoroughfare_leading_type" not in result:
            # Single token that is a directional only (rare edge case)
            result["thoroughfare_post_direction"] = dir_abbr
            tokens = []

    # --- Step 4: trailing type (English) — only when no leading type found ---
    if tokens and "thoroughfare_leading_type" not in result:
        last = tokens[-1]
        type_info = _lookup_type(last)
        if type_info and type_info[0] in ("trailing", "either"):
            result["thoroughfare_trailing_type"] = type_info[1]
            tokens = tokens[:-1]

    # --- Step 5: leading directional (English) — only when no leading type found ---
    if tokens and "thoroughfare_leading_type" not in result:
        first = tokens[0]
        dir_abbr = _lookup_directional(first)
        if dir_abbr:
            result["thoroughfare_pre_direction"] = dir_abbr
            tokens = tokens[1:]

    # --- Step 6: remainder is thoroughfare_name ---
    if tokens:
        result["thoroughfare_name"] = " ".join(_normalise(t) for t in tokens)
    elif not result:
        # Nothing was parsed at all — store original as name (fallback)
        result["thoroughfare_name"] = road.upper()

    # --- Fallback: if only directionals were extracted with no name ---
    if "thoroughfare_name" not in result and (
        "thoroughfare_leading_type" not in result
        and "thoroughfare_trailing_type" not in result
    ):
        result = {"thoroughfare_name": road.upper()}

    return result
```

- [ ] **Step 4: Run street splitter tests**

```bash
uv run pytest tests/unit/test_street_splitter.py -v --no-cov
```

Expected: all pass. If any fail, adjust the splitter logic — the tests define the contract.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/address_validator/services/street_splitter.py tests/unit/test_street_splitter.py --fix
uv run ruff format src/address_validator/services/street_splitter.py tests/unit/test_street_splitter.py
git add src/address_validator/services/street_splitter.py tests/unit/test_street_splitter.py \
        src/address_validator/canada_post_data/
git commit -m "#90 feat: add bilingual street component splitter"
```

---

## Task 4: libpostal_client.py

**Files:**
- Create: `src/address_validator/services/libpostal_client.py`
- Test: `tests/unit/test_libpostal_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_libpostal_client.py
"""Unit tests for LibpostalClient — httpx calls are mocked."""
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from address_validator.services.libpostal_client import (
    LibpostalClient,
    LibpostalUnavailableError,
)


@pytest.fixture
def client() -> LibpostalClient:
    return LibpostalClient(base_url="http://localhost:4400")


class TestTagMapping:
    async def test_english_address_mapped_to_iso_keys(self, client) -> None:
        raw_response = [
            {"label": "house_number", "value": "123"},
            {"label": "road", "value": "main st"},
            {"label": "city", "value": "seattle"},
            {"label": "state", "value": "wa"},
            {"label": "postcode", "value": "98101"},
        ]
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = httpx.Response(200, json=raw_response)
            result = await client.parse("123 Main St Seattle WA 98101")

        assert result["premise_number"] == "123"
        assert result["locality"] == "SEATTLE"
        assert result["administrative_area"] == "WA"
        assert result["postcode"] == "98101"
        # road should be split into thoroughfare components
        assert "thoroughfare_name" in result or "thoroughfare_trailing_type" in result

    async def test_french_address_maps_rue_as_leading_type(self, client) -> None:
        raw_response = [
            {"label": "house_number", "value": "350"},
            {"label": "road", "value": "rue des lilas ouest"},
            {"label": "city", "value": "quebec"},
            {"label": "state", "value": "qc"},
            {"label": "postcode", "value": "g1l 1b6"},
        ]
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = httpx.Response(200, json=raw_response)
            result = await client.parse("350 rue des Lilas Ouest, Quebec QC G1L 1B6")

        assert result["premise_number"] == "350"
        assert result["thoroughfare_leading_type"] == "RUE"
        assert result["locality"] == "QUEBEC"
        assert result["administrative_area"] == "QC"
        assert result["postcode"] == "G1L 1B6"

    async def test_country_label_dropped(self, client) -> None:
        raw_response = [
            {"label": "house_number", "value": "1"},
            {"label": "road", "value": "main st"},
            {"label": "country", "value": "canada"},
        ]
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = httpx.Response(200, json=raw_response)
            result = await client.parse("1 Main St Canada")

        assert "country" not in result

    async def test_connection_error_raises_unavailable(self, client) -> None:
        with patch.object(
            client._http, "get", side_effect=httpx.ConnectError("refused")
        ):
            with pytest.raises(LibpostalUnavailableError):
                await client.parse("123 Main St")

    async def test_timeout_raises_unavailable(self, client) -> None:
        with patch.object(
            client._http, "get", side_effect=httpx.TimeoutException("timeout")
        ):
            with pytest.raises(LibpostalUnavailableError):
                await client.parse("123 Main St")
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/unit/test_libpostal_client.py -v --no-cov
```

Expected: `ImportError`.

- [ ] **Step 3: Create libpostal_client.py**

```python
# src/address_validator/services/libpostal_client.py
"""Async client for the pelias/libpostal-service REST API.

Translates libpostal tag labels to ISO 19160-4 element names and
decomposes the composite ``road`` token via the bilingual street splitter.

The client holds a persistent ``httpx.AsyncClient`` connection.  Call
``aclose()`` during application shutdown (wired via lifespan in main.py).
"""

from __future__ import annotations

import logging

import httpx

from address_validator.services.street_splitter import split_road

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LibpostalUnavailableError(RuntimeError):
    """Raised when the libpostal sidecar cannot be reached."""


# ---------------------------------------------------------------------------
# libpostal label → ISO 19160-4 element name
# ---------------------------------------------------------------------------

_TAG_MAP: dict[str, str] = {
    "house_number": "premise_number",
    "house":        "premise_name",
    # "road" is handled separately via street_splitter
    "unit":         "sub_premise_number",
    "level":        "sub_premise_number",    # floor/level → sub-premise
    "staircase":    "sub_premise_number",
    "entrance":     "sub_premise_number",
    "po_box":       "general_delivery",
    "postcode":     "postcode",
    "suburb":       "dependent_locality",
    "city_district":"dependent_locality",
    "city":         "locality",
    "state_district":"dependent_locality",
    "state":        "administrative_area",
    # "country" is intentionally excluded — already known from request
}


def _map_tags(raw: list[dict[str, str]]) -> dict[str, str]:
    """Map a libpostal response list to an ISO 19160-4 component dict.

    The ``road`` label is passed through the street splitter.  All other
    labels are mapped via ``_TAG_MAP``; unknown labels are dropped.
    Values are uppercased to match our standardisation convention.
    """
    result: dict[str, str] = {}
    for item in raw:
        label = item.get("label", "")
        value = item.get("value", "").strip()
        if not value:
            continue
        if label == "road":
            result.update(split_road(value))
        elif label in _TAG_MAP:
            iso_key = _TAG_MAP[label]
            # postcode: preserve case (uppercase for normalisation)
            result[iso_key] = value.upper()
    return result


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LibpostalClient:
    """Async HTTP client wrapping the pelias/libpostal-service REST API."""

    def __init__(self, base_url: str = "http://localhost:4400") -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(5.0),
        )

    async def parse(self, address: str) -> dict[str, str]:
        """Parse *address* and return an ISO 19160-4 component dict.

        Raises ``LibpostalUnavailableError`` when the sidecar cannot be
        reached or returns a non-200 status.
        """
        try:
            response = await self._http.get(
                "/parse", params={"address": address}
            )
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("libpostal sidecar unavailable: %s", exc)
            raise LibpostalUnavailableError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("libpostal sidecar returned %s", exc.response.status_code)
            raise LibpostalUnavailableError(str(exc)) from exc

        return _map_tags(response.json())

    async def health_check(self) -> bool:
        """Return True if the sidecar is reachable and responds correctly."""
        try:
            result = await self.parse("1 main st")
            return bool(result)
        except LibpostalUnavailableError:
            return False

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_libpostal_client.py -v --no-cov
```

Expected: all pass. (The async tests require `pytest-anyio` or `anyio` — if failures occur due to async runner, add `@pytest.mark.anyio` or use `asyncio_mode = "auto"` in `pytest.ini`/`pyproject.toml`. Check existing async test patterns in the test suite first.)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/address_validator/services/libpostal_client.py tests/unit/test_libpostal_client.py --fix
uv run ruff format src/address_validator/services/libpostal_client.py tests/unit/test_libpostal_client.py
git add src/address_validator/services/libpostal_client.py tests/unit/test_libpostal_client.py
git commit -m "#90 feat: add LibpostalClient async httpx wrapper with ISO tag mapping"
```

---

## Task 5: parser.py — CA country dispatch + SUPPORTED_COUNTRIES_V2

**Files:**
- Modify: `src/address_validator/services/parser.py`
- Modify: `src/address_validator/routers/v1/core.py`

- [ ] **Step 1: Add SUPPORTED_COUNTRIES_V2 to core.py**

Read `src/address_validator/routers/v1/core.py`. The existing `SUPPORTED_COUNTRIES` is `frozenset({"US"})` and must remain unchanged for v1. Add a new constant for v2:

```python
# After the existing SUPPORTED_COUNTRIES line:
SUPPORTED_COUNTRIES_V2: frozenset[str] = frozenset({"US", "CA"})
```

Also add a `check_country_v2()` function mirroring `check_country()` but using `SUPPORTED_COUNTRIES_V2`:

```python
def check_country_v2(country: str) -> str:
    """Validate and normalise country for v2 endpoints (US, CA)."""
    country = country.upper()
    if country not in VALID_ISO2:
        raise APIError(
            status_code=422,
            error="invalid_country_code",
            message="Country must be a valid ISO 3166-1 alpha-2 code.",
        )
    if country not in SUPPORTED_COUNTRIES_V2:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=f"Currently supported: {', '.join(sorted(SUPPORTED_COUNTRIES_V2))}.",
        )
    return country
```

- [ ] **Step 2: Add LibpostalUnavailableError import and CA dispatch to parser.py**

Add the following import to `parser.py`:

```python
from address_validator.services.libpostal_client import (
    LibpostalClient,
    LibpostalUnavailableError,
)
```

Then make `parse_address()` async and add the CA dispatch:

```python
async def parse_address(
    raw: str,
    country: str = "US",
    libpostal_client: LibpostalClient | None = None,
) -> ParseResponseV1:
    """Parse *raw* address string into labelled components.

    For ``country="CA"``, delegates to the libpostal sidecar via
    *libpostal_client*.  Raises ``LibpostalUnavailableError`` (→ 503)
    when the client is None or unreachable.

    For ``country="US"``, uses the existing usaddress path unchanged.
    """
    if country == "CA":
        if libpostal_client is None:
            raise LibpostalUnavailableError("No libpostal client configured")
        set_audit_context(parse_type="libpostal")
        components = await libpostal_client.parse(raw)
        return ParseResponseV1(
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
    return _parse(raw, country)
```

**Important:** `_parse()` is currently synchronous. It can remain synchronous; `parse_address()` is now `async` but calls the sync `_parse()` directly (no `await` needed — sync functions are callable from async context without `run_in_executor` for the event-loop-safe case at our traffic levels).

Verify that all callers of `parse_address()` (v1 parse router, v2 parse router, v1 validate router) are updated to `await parse_address(...)`.

- [ ] **Step 3: Update all callers to await parse_address()**

Read and update:
- `src/address_validator/routers/v1/parse.py` — `parse_address(...)` → `await parse_address(...)`
- `src/address_validator/routers/v2/parse.py` — same
- `src/address_validator/routers/v1/validate.py` — `parse_address(...)` → `await parse_address(...)`

v1 callers do not pass `libpostal_client` (it defaults to `None`). CA requests in v1 are blocked by `check_country()` before reaching `parse_address()`.

- [ ] **Step 4: Update v2 parse router to use check_country_v2 and pass libpostal_client**

In `src/address_validator/routers/v2/parse.py`:

```python
from address_validator.routers.v1.core import check_country_v2
# ...

async def parse(req, request, component_profile) -> ParseResponseV2:
    # ...
    check_country_v2(req.country)   # was check_country()
    libpostal_client = getattr(request.app.state, "libpostal_client", None)
    result = await parse_address(req.address.strip(), country=req.country,
                                  libpostal_client=libpostal_client)
    # ...
```

- [ ] **Step 5: Handle LibpostalUnavailableError in v2 parse router**

```python
from address_validator.services.libpostal_client import LibpostalUnavailableError

# In the parse handler, wrap the parse call:
try:
    result = await parse_address(...)
except LibpostalUnavailableError:
    raise APIError(
        status_code=503,
        error="parsing_unavailable",
        message=(
            "Address parsing for CA is currently unavailable. "
            "Try again shortly or provide pre-parsed components via /validate."
        ),
    )
```

- [ ] **Step 6: Wire LibpostalClient in main.py lifespan**

Read `src/address_validator/main.py` to locate the lifespan context manager. Add:

```python
from address_validator.services.libpostal_client import LibpostalClient

# In lifespan startup (after engine init):
libpostal_url = os.getenv("LIBPOSTAL_URL", "http://localhost:4400")
libpostal_client = LibpostalClient(base_url=libpostal_url)
if await libpostal_client.health_check():
    logger.info("libpostal sidecar reachable at %s", libpostal_url)
else:
    logger.warning(
        "libpostal sidecar not reachable at %s — CA parsing will return 503",
        libpostal_url,
    )
app.state.libpostal_client = libpostal_client

# In lifespan shutdown:
await libpostal_client.aclose()
```

- [ ] **Step 7: Run full test suite**

```bash
uv run pytest --no-cov -x
```

Expected: all pass. The libpostal health check in lifespan will fail during tests (no sidecar running), but the warning is non-fatal. The `libpostal_client` will be set to a `LibpostalClient` instance whose `parse()` will fail — but CA parse routes are not exercised by existing tests yet.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/ --fix && uv run ruff format src/
git add src/address_validator/services/parser.py \
        src/address_validator/routers/v1/core.py \
        src/address_validator/routers/v1/parse.py \
        src/address_validator/routers/v1/validate.py \
        src/address_validator/routers/v2/parse.py \
        src/address_validator/main.py
git commit -m "#90 feat: add CA parse dispatch via libpostal + SUPPORTED_COUNTRIES_V2"
```

---

## Task 6: Integration tests for CA parsing

**Files:**
- Create: `tests/integration/test_v2_parse_ca.py`

These tests mock the libpostal client — no live sidecar required.

- [ ] **Step 1: Write tests**

```python
# tests/integration/test_v2_parse_ca.py
"""Integration tests for POST /api/v2/parse with country=CA."""
import pytest
from unittest.mock import AsyncMock, patch


class TestV2ParseCA:
    def test_ca_address_returns_200_with_iso_keys(self, client) -> None:
        mock_components = {
            "premise_number": "350",
            "thoroughfare_leading_type": "RUE",
            "thoroughfare_name": "DES LILAS",
            "thoroughfare_post_direction": "O",
            "locality": "QUEBEC",
            "administrative_area": "QC",
            "postcode": "G1L 1B6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_components,
        ):
            response = client.post(
                "/api/v2/parse",
                json={
                    "address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        body = response.json()
        values = body["components"]["values"]
        assert values["premise_number"] == "350"
        assert values["thoroughfare_leading_type"] == "RUE"
        assert values["locality"] == "QUEBEC"
        assert values["administrative_area"] == "QC"
        assert values["postcode"] == "G1L 1B6"
        assert body["api_version"] == "2"
        assert body["country"] == "CA"

    def test_ca_not_available_in_v1(self, client) -> None:
        response = client.post(
            "/api/v1/parse",
            json={"address": "123 Main St", "country": "CA"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_libpostal_unavailable_returns_503(self, client) -> None:
        from address_validator.services.libpostal_client import LibpostalUnavailableError
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            side_effect=LibpostalUnavailableError("refused"),
        ):
            response = client.post(
                "/api/v2/parse",
                json={"address": "123 Main St", "country": "CA"},
            )
        assert response.status_code == 503
        assert response.json()["error"] == "parsing_unavailable"

    def test_ca_with_usps_profile_returns_translated_keys(self, client) -> None:
        mock_components = {
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
            return_value=mock_components,
        ):
            response = client.post(
                "/api/v2/parse?component_profile=usps-pub28",
                json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
            )
        values = response.json()["components"]["values"]
        assert values["address_number"] == "123"
        assert values["street_name"] == "MAIN"
        assert values["city"] == "TORONTO"
        assert values["state"] == "ON"
        assert values["zip_code"] == "M5V 2T6"
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/integration/test_v2_parse_ca.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 3: Run full suite**

```bash
uv run pytest --no-cov -x
```

- [ ] **Step 4: Coverage check**

```bash
uv run pytest --cov --cov-report=term-missing
```

Expected: ≥ 80%.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check tests/integration/test_v2_parse_ca.py --fix
uv run ruff format tests/integration/test_v2_parse_ca.py
git add tests/integration/test_v2_parse_ca.py
git commit -m "#90 test: add v2 CA parse integration tests with mocked libpostal"
```

---

## Verification

After all tasks, verify against the running dev server (with libpostal sidecar running):

```bash
# CA address — raw string parse via libpostal
curl -s -X POST http://localhost:8001/api/v2/parse \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6", "country": "CA"}' \
  | jq '{components: .components.values, type: .type}'

# CA in v1 — should 422
curl -s -X POST http://localhost:8001/api/v1/parse \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St", "country": "CA"}' \
  | jq .error

# US still works in v2
curl -s -X POST http://localhost:8001/api/v2/parse \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St, Seattle, WA 98101"}' \
  | jq '.components.values | keys'
```

---

**Plan 3 prerequisite:** This plan must be merged. Plan 3 adds `canada_post_data/` province and suffix tables and wires CA standardization, completing full CA pipeline parity.
