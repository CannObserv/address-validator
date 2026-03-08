# Address Validation Technical Survey
## Phase 3: Real Physical Location Validation (CASS-Style)

**Date:** January 2025  
**Context:** FastAPI service with existing USPS Pub 28 parsing/standardization  
**Goal:** Validate addresses represent real, deliverable physical locations

---

## Table of Contents
1. [Python Libraries (OSS)](#1-python-libraries-oss)
2. [USPS Official APIs](#2-usps-official-apis)
3. [Google APIs](#3-google-apis)
4. [Other Commercial APIs](#4-other-commercial-apis)
5. [Key Technical Concepts](#5-key-technical-concepts)
6. [FastAPI Integration Considerations](#6-fastapi-integration-considerations)
7. [Cost Comparison](#7-cost-comparison)
8. [Recommendations](#8-recommendations)

---

## 1. Python Libraries (OSS)

### Critical Upfront Truth

**No pure Python OSS library can confirm a real physical address exists without hitting an external database.** This is architecturally impossible because:

1. The USPS Address Matching System (AMS) database is proprietary and licensed
2. DPV (Delivery Point Validation) data is only available to CASS-certified vendors
3. The database contains ~160M delivery points and is updated monthly
4. USPS legally restricts redistribution of the underlying data

OSS libraries can only **parse** and **standardize/normalize** addresses—not validate existence.

---

### 1.1 usaddress-scourgify

**Package:** `usaddress-scourgify` (PyPI)  
**Current Version:** 0.6.0  
**Status:** Active (maintained by GreenBuildingRegistry)

**Capabilities:**
- Wraps `usaddress` (CRF-based parser) with standardization
- Normalizes to USPS Pub 28 format
- Applies RESO (Real Estate Standards Organization) guidelines
- Handles directionals, suffixes, unit designators

**What it does:**
```python
from scourgify import normalize_address_record

result = normalize_address_record("123 Main Street Northwest, Apt 4B")
# Returns standardized components: {address_line_1, address_line_2, city, state, postal_code}
```

**Limitations:**
- ❌ No CASS certification (impossible for OSS)
- ❌ Cannot validate address exists
- ❌ Cannot assign ZIP+4
- ❌ No DPV, LACSLink, SuiteLink
- ⚠️ Formatting only—no deliverability confirmation

**CASS Status:** Not applicable (parsing library only)

---

### 1.2 pyusps

**Package:** `pyusps`  
**Current Version:** 0.0.7  
**Last Updated:** January 2018 (7+ years ago)  
**Status:** ⚠️ **ABANDONED / EFFECTIVELY DEAD**

**What it was:**
- Wrapper around USPS Web Tools XML API
- Required USPS Web Tools registration

**Why it's unusable:**
1. Not updated for 7 years
2. USPS deprecated the XML API it targets
3. Doesn't support new OAuth2-based APIs
4. No Python 3.10+ testing
5. Dependencies are outdated

**Verdict:** Do not use. Write your own client if you need USPS API access.

---

### 1.3 deepparse

**Package:** `deepparse`  
**Current Version:** 0.10.0  
**Status:** Active (Université de Montréal research project)

**Capabilities:**
- Deep learning-based address **parsing** (seq2seq models)
- Multinational support (not US-specific)
- Can use pre-trained or custom models
- Extracts: StreetNumber, StreetName, Unit, Municipality, Province, PostalCode, etc.

**What it does:**
```python
from deepparse.parser import AddressParser

parser = AddressParser(model_type="bpemb")  # or "fasttext"
parsed = parser("123 Main St, Springfield, IL 62701")
```

**Limitations:**
- ❌ **No real-location validation whatsoever**
- ❌ No CASS, DPV, or any deliverability data
- ❌ Not trained on USPS conventions specifically
- ⚠️ Heavy dependencies (PyTorch)
- ⚠️ Slower than CRF-based parsers (usaddress)

**CASS Status:** Not applicable (ML parsing library only)

**Verdict:** Useful for multinational parsing. Overkill for US-only. Cannot validate existence.

---

### 1.4 postal (libpostal)

**Package:** `postal` (pypostal)  
**Current Version:** 1.1.11  
**Status:** Active (OpenVenues project)

**Capabilities:**
- Bindings to `libpostal` C library
- Trained on OpenStreetMap + OpenAddresses data
- International address parsing and normalization
- Very fast (C implementation)

**Limitations:**
- ❌ **Cannot validate existence** (parsing/normalization only)
- ❌ No CASS or DPV
- ⚠️ Requires compiling libpostal (2GB+ data download)
- ⚠️ Complex installation

---

### 1.5 usaddress (already in your stack)

**Package:** `usaddress`  
**Current Version:** 0.5.16  
**Status:** Stable (DataMade)

You already use this. It's a CRF-based parser. Same limitations: **parsing only, no validation**.

---

### 1.6 Other Python Packages

| Package | Purpose | Validates Existence? |
|---------|---------|---------------------|
| `street-address` | Regex-based US parsing | ❌ No |
| `address-parser` | NLP parsing | ❌ No |
| `addressable` | Formatting | ❌ No |

---

### 1.7 Summary: OSS Python Libraries

**Bottom line:** To validate a real physical address, you MUST call an external API that has access to USPS AMS/DPV databases. There is no alternative.

---

## 2. USPS Official APIs

### 2.1 USPS Web Tools Address API (Legacy XML)

**Status:** ⚠️ **DEPRECATED** — Do not use for new development

**Endpoint:** `https://secure.shippingapis.com/ShippingAPI.dll`

**History:**
- Launched ~2004
- XML-based request/response
- Required User ID registration
- USPS announced deprecation in 2023

**Why avoid:**
- Being phased out in favor of REST APIs
- No modern auth (no OAuth2)
- Limited rate limiting transparency
- XML parsing overhead

---

### 2.2 USPS Addresses API v3 (Current)

**Status:** ✅ **CURRENT / RECOMMENDED**

**Base URL:** `https://apis.usps.com/addresses/v3/`

**Endpoints:**
| Endpoint | Purpose |
|----------|--------|
| `POST /address` | Validate single address |
| `POST /addresses` | Validate batch (up to 5) |
| `GET /city-state/{zipCode}` | Lookup city/state from ZIP |
| `GET /zipcode` | Lookup ZIP from city/state |

**Authentication:**
- OAuth 2.0 Client Credentials flow
- Token endpoint: `https://apis.usps.com/oauth2/v3/token`
- Tokens valid for 1 hour
- Requires Consumer Key + Consumer Secret

**Registration Process:**
1. Create account at https://developer.usps.com
2. Create an "App" in the developer portal
3. Request access to Addresses API
4. Approval is typically instant for basic tier
5. No CASS certification required to USE the API (only to PROVIDE a certified service)

**Rate Limits:**
| Tier | Calls/Day | Calls/Second |
|------|-----------|-------------|
| Free | 10,000 | 5 |
| Enterprise | Negotiated | Higher |

**Response Fields:**
```json
{
  "firm": "ACME CORP",
  "address": {
    "streetAddress": "123 MAIN ST",
    "streetAddressAbbreviation": "123 MAIN ST",
    "secondaryAddress": "APT 4B",
    "city": "SPRINGFIELD",
    "cityAbbreviation": "SPRINGFIELD",
    "state": "IL",
    "ZIPCode": "62701",
    "ZIPPlus4": "1234"
  },
  "addressAdditionalInfo": {
    "deliveryPoint": "12",
    "carrierRoute": "C001",
    "DPVConfirmation": "Y",
    "DPVCMRA": "N",
    "business": "Y",
    "centralDeliveryPoint": "N",
    "vacant": "N"
  },
  "corrections": [],
  "matches": []
}
```

**Key Response Fields:**
- `DPVConfirmation`: Y/D/S/N (see Section 5.1)
- `DPVCMRA`: Is it a Commercial Mail Receiving Agency (e.g., UPS Store)?
- `vacant`: Is the address currently vacant?
- `business`: Is this a business address?
- `carrierRoute`: USPS carrier route code
- `deliveryPoint`: 2-digit delivery point barcode

**CASS Status:** The API itself uses CASS-certified backend processing. As a CONSUMER, you don't need CASS certification. You would only need certification if you were building and selling your own address validation product.

**Limitations:**
- ❌ No LACSLink data exposed in response
- ❌ No SuiteLink data exposed in response
- ❌ Limited footnote detail compared to commercial providers
- ⚠️ 10K/day free tier may be insufficient for high-volume

**Cost:** FREE (within rate limits)

---

### 2.3 USPS eCommerce APIs

**Relevance:** Limited for pure address validation

USPS eCommerce APIs are focused on:
- Shipping labels
- Tracking
- Rate calculation
- Pickup scheduling

They do include address validation as part of label creation, but you'd use the dedicated Addresses API for standalone validation.

---

### 2.4 What "CASS Certified" Actually Means

**CASS = Coding Accuracy Support System**

**For Software VENDORS (SmartyStreets, Melissa, etc.):**
- Must license USPS AMS database
- Must pass USPS accuracy tests (98%+ match rate)
- Must recertify annually
- Must process through approved software
- Certification allows them to provide "CASS Certified" mail processing

**For API CONSUMERS (you):**
- You do NOT need CASS certification to use USPS API
- You do NOT need certification to use SmartyStreets/Melissa APIs
- CASS certification only matters if you're providing address correction as a service to mailers seeking postal discounts

**Related certifications:**
- **NCOA (National Change of Address):** For move-update processing
- **DPV:** Part of CASS—validates specific delivery points
- **LACSLink:** Rural route conversion (part of CASS)
- **SuiteLink:** Secondary address appending (part of CASS)

---

## 3. Google APIs

### 3.1 Address Validation API

**Status:** ✅ **Generally Available (GA)**

**Endpoint:** `https://addressvalidation.googleapis.com/v1:validateAddress`

**Capabilities:**
- Full address validation with component-level detail
- USPS CASS data for US addresses (when enabled)
- International validation (40+ countries)
- Geocoding included in response
- Verdict system (GRANULARITY + VALIDATION levels)

**Request:**
```json
{
  "address": {
    "regionCode": "US",
    "addressLines": ["123 Main St, Apt 4B, Springfield, IL 62701"]
  },
  "enableUspsCass": true
}
```

**Response Structure:**
```json
{
  "result": {
    "verdict": {
      "inputGranularity": "PREMISE",
      "validationGranularity": "PREMISE",
      "geocodeGranularity": "PREMISE",
      "addressComplete": true,
      "hasUnconfirmedComponents": false,
      "hasInferredComponents": false
    },
    "address": {
      "formattedAddress": "123 Main St, Apt 4B, Springfield, IL 62701",
      "postalAddress": { ... },
      "addressComponents": [
        {
          "componentName": { "text": "123", "languageCode": "en" },
          "componentType": "street_number",
          "confirmationLevel": "CONFIRMED"
        }
        // ... more components
      ]
    },
    "geocode": {
      "location": { "latitude": 39.7817, "longitude": -89.6501 },
      "plusCode": { ... },
      "bounds": { ... }
    },
    "uspsData": {
      "standardizedAddress": {
        "firstAddressLine": "123 MAIN ST APT 4B",
        "cityStateZipAddressLine": "SPRINGFIELD IL 62701-1234"
      },
      "deliveryPointCode": "12",
      "deliveryPointCheckDigit": "3",
      "dpvConfirmation": "Y",
      "dpvFootnote": "AABB",
      "dpvCmra": "N",
      "dpvVacant": "N",
      "dpvNoStat": "N",
      "carrierRoute": "C001",
      "carrierRouteIndicator": "D",
      "postOfficeCity": "SPRINGFIELD",
      "postOfficeState": "IL",
      "fipsCountyCode": "17167",
      "county": "SANGAMON",
      "elotNumber": "0001",
      "elotFlag": "A",
      "addressRecordType": "S"
    }
  }
}
```

**USPS CASS Data Fields (when `enableUspsCass: true`):**
- `dpvConfirmation`: Y/D/S/N
- `dpvFootnote`: Detailed footnote codes (AA, BB, CC, etc.)
- `dpvCmra`: Commercial Mail Receiving Agency flag
- `dpvVacant`: Vacancy indicator
- `dpvNoStat`: No-stat indicator (new construction, etc.)
- `carrierRoute`: Carrier route code
- `elotNumber`: Enhanced Line of Travel number
- `addressRecordType`: H (highrise), S (street), etc.

**Pricing (as of Jan 2025):**

| Volume/month | Price per call |
|--------------|---------------|
| 0 - 10,000 | $0.00 (free) |
| 10,001 - 100,000 | $0.005 |
| 100,001+ | $0.004 |

**Monthly credit:** $200 (covers first ~40K calls after free tier)

**Important:** The free tier is per billing account. USPS CASS validation (`enableUspsCass: true`) is only available for US addresses.

**Latency:** ~100-300ms typical

**SLA:** 99.9% availability (with SLA contract)

---

### 3.2 Geocoding API

**Endpoint:** `https://maps.googleapis.com/maps/api/geocode/json`

**Can it validate addresses?** Sort of, but poorly.

**What it does:**
- Converts addresses to lat/lng coordinates
- Returns match quality indicators

**Limitations vs. Address Validation API:**
- ❌ No USPS CASS data
- ❌ No DPV confirmation
- ❌ No vacancy or residential flags
- ❌ Geocodes "close enough" matches without flagging issues
- ❌ May return rooftop coordinates for non-existent unit numbers
- ⚠️ Cannot distinguish "123 Main St Apt 1" from "123 Main St Apt 999"

**Use case:** If you need coordinates for a validated address, use the Address Validation API (which includes geocoding).

**Pricing:** $0.005 per request (after free tier)

---

### 3.3 Places API

**Relevance:** Minimal for address validation

**Use cases:**
- Autocomplete (place predictions)
- Place details (for businesses/POIs)
- Nearby search

**Not suitable for:** Validating residential addresses, CASS compliance, deliverability checking.

---

### 3.4 Google Pricing Summary

| API | Free Tier | Then |
|-----|-----------|------|
| Address Validation | 10,000/mo | $0.005 (100K), $0.004 (100K+) |
| Geocoding | $200/mo credit | $0.005/call |
| Places Autocomplete | $200/mo credit | $0.00283/call (session) |

**Note:** Google provides a $200/month credit that applies across all Maps Platform APIs.

---

## 4. Other Commercial APIs

### 4.1 Smarty (formerly SmartyStreets)

**Status:** ✅ **Industry Leader for US Address Validation**

**Product:** US Street Address API

**Capabilities:**
- ✅ CASS Certified (recertified annually)
- ✅ Full DPV confirmation (Y/S/D/N with footnotes)
- ✅ LACSLink (rural-to-street conversion)
- ✅ SuiteLink (secondary address append)
- ✅ RDI (Residential Delivery Indicator)
- ✅ Vacancy indicator (updated monthly)
- ✅ eLOT (Enhanced Line of Travel)
- ✅ Batch processing (up to 100 addresses/request)

**Endpoint:** `https://us-street.api.smarty.com/street-address`

**Response Fields:**
```json
{
  "input_index": 0,
  "delivery_line_1": "123 MAIN ST APT 4B",
  "last_line": "SPRINGFIELD IL 62701-1234",
  "components": {
    "primary_number": "123",
    "street_name": "MAIN",
    "street_suffix": "ST",
    "secondary_designator": "APT",
    "secondary_number": "4B",
    "city_name": "SPRINGFIELD",
    "state_abbreviation": "IL",
    "zipcode": "62701",
    "plus4_code": "1234",
    "delivery_point": "12",
    "delivery_point_check_digit": "3"
  },
  "metadata": {
    "record_type": "S",
    "zip_type": "Standard",
    "county_fips": "17167",
    "county_name": "Sangamon",
    "carrier_route": "C001",
    "congressional_district": "13",
    "rdi": "Residential",
    "elot_sequence": "0001",
    "elot_sort": "A",
    "latitude": 39.78170,
    "longitude": -89.65010,
    "precision": "Zip9",
    "time_zone": "Central",
    "utc_offset": -6,
    "dst": true
  },
  "analysis": {
    "dpv_match_code": "Y",
    "dpv_footnotes": "AABB",
    "dpv_cmra": "N",
    "dpv_vacant": "N",
    "dpv_no_stat": "N",
    "active": "Y",
    "footnotes": "N#",
    "lacslink_code": "",
    "lacslink_indicator": "",
    "suitelink_match": false
  }
}
```

**Python SDK:**
```python
from smartystreets_python_sdk import StaticCredentials, ClientBuilder
from smartystreets_python_sdk.us_street import Lookup

credentials = StaticCredentials(auth_id, auth_token)
client = ClientBuilder(credentials).build_us_street_api_client()

lookup = Lookup()
lookup.street = "123 Main St"
lookup.city = "Springfield"
lookup.state = "IL"

client.send_lookup(lookup)
result = lookup.result[0]
print(result.analysis.dpv_match_code)  # 'Y'
```

**Pricing (as of Jan 2025):**

| Plan | Lookups/mo | Price/mo | Per-lookup |
|------|-----------|----------|------------|
| Free | 250 | $0 | N/A |
| Starter | 5,000 | $25 | $0.005 |
| Pro | 25,000 | $99 | $0.00396 |
| Business | 100,000 | $349 | $0.00349 |
| Enterprise | Custom | Custom | ~$0.002-0.003 |

**Free tier:** 250 lookups/month (very limited, essentially for testing)

**Latency:** ~50-150ms typical (very fast)

---

### 4.2 Melissa Data (Melissa)

**Status:** ✅ **Enterprise-grade, CASS Certified**

**Product:** Global Address Verification

**Capabilities:**
- ✅ CASS Certified
- ✅ Full DPV/LACSLink/SuiteLink
- ✅ International addresses (240+ countries)
- ✅ Batch and real-time APIs
- ✅ On-premise deployment options

**Endpoint:** `https://address.melissadata.net/v3/WEB/GlobalAddress/doGlobalAddress`

**Python Support:** REST API (no official SDK, but straightforward HTTP calls)

**Pricing:**
- Credit-based system
- 1,000 free credits on signup
- Enterprise pricing: Contact sales
- Typical: $0.01-0.03 per lookup depending on volume

**Best for:** Enterprise with international requirements

**Downside:** Less transparent pricing than Smarty

---

### 4.3 Lob.com

**Status:** ✅ **Good choice for mail-focused applications**

**Product:** Address Verification API

**Capabilities:**
- ✅ CASS Certified
- ✅ DPV confirmation
- ✅ Deliverability scoring
- ✅ Integrates with Lob's mail printing/sending

**Endpoint:** `POST https://api.lob.com/v1/us_verifications`

**Response includes:**
- `deliverability`: "deliverable", "deliverable_missing_unit", "deliverable_unnecessary_unit", "undeliverable"
- `dpv_confirmation`: Y/D/S/N
- `dpv_cmra`: Boolean
- `dpv_vacant`: Boolean
- `lacs_indicator`: LACSLink result
- `suite_return_code`: SuiteLink result

**Pricing:**

| Volume/mo | Price/verification |
|-----------|-------------------|
| 0 - 2,000 | $0.04 |
| 2,001 - 10,000 | $0.035 |
| 10,001 - 50,000 | $0.03 |
| 50,001 - 100,000 | $0.025 |
| 100,001+ | Custom |

**No free tier for verification-only usage.**

**Best for:** If you're also using Lob for physical mail sending

---

### 4.4 EasyPost

**Product:** Address Verification (as part of shipping API)

**Relevance:** Moderate. EasyPost is primarily a shipping aggregator. Address verification is included but not their focus.

**Endpoint:** `POST https://api.easypost.com/v2/addresses`

**Response includes:**
- `verifications.delivery.success`: Boolean
- `verifications.delivery.errors`: Array of issues

**Pricing:** 
- Verification included with shipping label purchases
- Standalone verification: $0.01/address

**Best for:** If you're already using EasyPost for shipping

---

### 4.5 Other Providers (Brief Mentions)

| Provider | CASS? | Notes |
|----------|-------|-------|
| **Pitney Bowes** | ✅ | Enterprise-focused, complex pricing, strong international |
| **HERE** | ❌ | Geocoding-focused, no true CASS |
| **AWS Location Service** | ❌ | Uses Esri/HERE backends, no CASS |
| **PostGrid** | ✅ | Canadian focus, US support, competitive pricing |
| **address-validator.net** | ❌ | Aggregator, unclear backend |
| **Experian QAS** | ✅ | Enterprise, contact sales |

---

## 5. Key Technical Concepts

### 5.1 DPV (Delivery Point Validation)

**What it is:** The definitive check that an address is a real USPS delivery point.

**DPV Match Codes:**

| Code | Meaning |
|------|---------|
| **Y** | Confirmed; address is a valid delivery point |
| **S** | Confirmed, secondary (apt/suite) required but missing |
| **D** | Confirmed for primary, secondary provided but not confirmed |
| **N** | Not confirmed; address may not exist |
| (blank) | Address not found in DPV database |

**Example scenarios:**
- `Y`: "123 Main St Apt 4B" — fully validated
- `S`: "123 Main St" — building exists, but needs apt number
- `D`: "123 Main St Apt 999" — building exists, apt 999 doesn't
- `N`: "999 Fake St" — no such address

**DPV Footnotes (USPS standard):**
- `AA`: Street address matched
- `A1`: No street address match
- `BB`: Route/box number matched
- `CC`: Secondary matched
- `C1`: Secondary required but missing
- `N1`: Secondary not confirmed
- `F1`: Military address matched
- `G1`: General delivery matched
- `U1`: Unique ZIP matched

---

### 5.2 LACSLink (Locatable Address Conversion System)

**What it does:** Converts rural-style addresses to street-style addresses.

**Example:**
- Input: "RR 1 BOX 234, RURALTOWN, KS 67890"
- Output: "1234 COUNTY ROAD 500, RURALTOWN, KS 67890"

**Why it matters:** USPS has been converting rural routes to street addresses for 911 compatibility. LACSLink ensures old addresses still get mail.

**LACSLink Return Codes:**
- `A`: Match, new address returned
- `00`: No match (address wasn't converted)
- `09`: Match, no new address available
- `14`: Found, but undeliverable
- `92`: Partial match

---

### 5.3 SuiteLink

**What it does:** Appends missing secondary (suite/apt) information for business addresses.

**Example:**
- Input: "ACME CORP, 500 OFFICE PARK DR, SPRINGFIELD, IL 62701"
- Output: "ACME CORP, 500 OFFICE PARK DR STE 300, SPRINGFIELD, IL 62701"

**Only works for:** Business addresses where the firm name is known and has a suite on file.

---

### 5.4 RDI (Residential Delivery Indicator)

**What it is:** Classifies address as Residential or Commercial.

**Values:**
- `Y` or `Residential`: Home address
- `N` or `Commercial`: Business address
- (blank): Unknown

**Why it matters:**
- Shipping carriers charge differently for residential vs. commercial
- Some services (e.g., FedEx Ground) have residential surcharges
- Fraud detection (business registration at residential address)

---

### 5.5 Vacancy Indicator

**What it is:** Indicates if USPS has flagged the address as vacant.

**Updated:** Monthly via DSF2 (Delivery Sequence File 2nd Generation)

**Caveats:**
- Only means "no mail delivery at this address for 90+ days"
- New construction may show as vacant
- Snowbirds may show as vacant
- Not a guarantee of physical occupancy

---

### 5.6 ZIP+4 Assignment

**What it is:** The full 9-digit ZIP code (e.g., 62701-1234).

**Why it matters:**
- Required for maximum postal discounts
- Uniquely identifies a delivery point (or small group)
- ZIP+4 + delivery point = unique barcode

**Assignment:** The validation service assigns ZIP+4 based on USPS database.

---

### 5.7 CASS Certification Summary

**CASS = Coding Accuracy Support System**

**What CASS-certified software must provide:**
1. Address standardization (Pub 28)
2. ZIP+4 assignment (98%+ accuracy)
3. Carrier route assignment
4. DPV confirmation
5. LACSLink conversion
6. SuiteLink appending
7. eLOT sequencing

**Related USPS certifications:**
- **NCOALink**: National Change of Address (move updates)
- **DSF2**: Delivery Sequence File (sequencing, vacancy)
- **ACS**: Address Change Service (returned mail processing)

---

## 6. FastAPI Integration Considerations

### 6.1 Async HTTP Client Compatibility

**Recommendation:** Use `httpx` (already in your dev dependencies)

```python
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create shared client on startup
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
    )
    yield
    await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)
```

**Why httpx over requests:**
- Native async support
- Connection pooling
- HTTP/2 support
- Similar API to requests
- Already in your test dependencies

**Note:** Smarty's Python SDK uses `requests` (synchronous). For FastAPI, you'd either:
1. Wrap SDK calls in `run_in_executor`
2. Use httpx directly against their REST API (recommended)

---

### 6.2 Rate Limiting / Throttling

**Approaches:**

```python
import asyncio
from collections import deque
from time import time

class RateLimiter:
    """Token bucket rate limiter."""
    def __init__(self, rate: float, burst: int):
        self.rate = rate  # requests per second
        self.burst = burst
        self.tokens = burst
        self.last_update = time()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        async with self._lock:
            now = time()
            self.tokens = min(
                self.burst,
                self.tokens + (now - self.last_update) * self.rate
            )
            self.last_update = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1

# Usage
usps_limiter = RateLimiter(rate=5.0, burst=10)  # 5/sec with burst of 10
smartly_limiter = RateLimiter(rate=100.0, burst=100)  # Higher limit
```

**Provider-specific limits:**
| Provider | Rate Limit | Recommendation |
|----------|------------|----------------|
| USPS v3 | 5/sec (free) | Conservative limiting |
| Smarty | 100/sec typical | Usually not a bottleneck |
| Google | 100/sec (per project) | Reasonable default |
| Lob | 150/sec | Generous |

---

### 6.3 Caching Strategies

**Safe to cache:**
- Standardized address (address → components)
- ZIP+4 assignment (stable)
- DPV confirmation (Y/N)
- RDI (residential/commercial)
- Geocoordinates
- FIPS codes

**NOT safe to cache long-term:**
- Vacancy indicator (changes frequently)
- Business names (move, close)
- CMRA status (can change)

**Cache key strategy:**
```python
import hashlib

def make_cache_key(address: str, city: str, state: str, zip_code: str) -> str:
    """Normalize and hash for cache key."""
    normalized = f"{address.upper().strip()}|{city.upper().strip()}|{state.upper().strip()}|{zip_code.strip()}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
```

**Cache TTLs:**
| Field | Recommended TTL |
|-------|----------------|
| Standardization | 30-90 days |
| DPV confirmation | 30 days |
| Vacancy | DO NOT CACHE (or 24h max) |
| Geocode | 90+ days |

**Redis implementation:**
```python
import redis.asyncio as redis
import json

class AddressCache:
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)
    
    async def get(self, cache_key: str) -> dict | None:
        data = await self.redis.get(f"addr:{cache_key}")
        return json.loads(data) if data else None
    
    async def set(self, cache_key: str, result: dict, ttl_seconds: int = 86400 * 30):
        await self.redis.setex(
            f"addr:{cache_key}",
            ttl_seconds,
            json.dumps(result)
        )
```

---

### 6.4 Error Handling / Fallback Chains

**Recommended pattern:**

```python
from enum import Enum
from typing import Optional
import httpx

class ValidationProvider(Enum):
    USPS = "usps"
    SMARTY = "smarty"
    GOOGLE = "google"

class AddressValidator:
    def __init__(self, providers: list[ValidationProvider]):
        self.providers = providers  # Ordered by preference
    
    async def validate(
        self,
        address: str,
        city: str,
        state: str,
        zip_code: str
    ) -> ValidationResult:
        last_error = None
        
        for provider in self.providers:
            try:
                result = await self._call_provider(provider, address, city, state, zip_code)
                result.provider = provider
                return result
            except httpx.TimeoutException as e:
                last_error = e
                continue  # Try next provider
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limited
                    last_error = e
                    continue
                raise  # Don't fallback on other HTTP errors
        
        raise ValidationUnavailable(f"All providers failed: {last_error}")
```

**Error types to handle:**
- `Timeout`: Network issues → fallback
- `429 Too Many Requests`: Rate limited → fallback or queue
- `401/403`: Auth issues → do NOT fallback, fix config
- `400`: Bad input → return validation error to client
- `5xx`: Provider issues → fallback

---

### 6.5 Latency Expectations

| Provider | Typical P50 | Typical P99 | Notes |
|----------|-------------|-------------|-------|
| USPS v3 | 150ms | 500ms | Can spike during high load |
| Smarty | 50ms | 200ms | Very consistent |
| Google | 100ms | 400ms | Includes geocoding |
| Lob | 100ms | 300ms | Consistent |
| Melissa | 100ms | 400ms | Varies by endpoint |

**Client timeout recommendations:**
- Connect timeout: 3-5 seconds
- Read timeout: 10 seconds
- Total timeout: 15 seconds

---

### 6.6 Sync vs Async Response Patterns

**Synchronous (recommended for most cases):**
```
POST /v1/validate
→ 200 OK {"dpv_match": "Y", ...}
```

Latency is typically acceptable (<500ms).

**Webhook pattern (for batch processing):**
```
POST /v1/validate/batch
→ 202 Accepted {"job_id": "abc123"}

# Later, webhook calls your endpoint:
POST /your-callback-url
{"job_id": "abc123", "results": [...]}
```

**When to use webhooks:**
- Processing 100+ addresses at once
- Background list cleaning
- Non-interactive batch jobs

---

## 7. Cost Comparison

### 7.1 Cost Table (Monthly)

| Provider | 10K/mo | 100K/mo | 1M/mo | Free Tier |
|----------|--------|---------|-------|-----------|
| **USPS v3** | $0 | $0* | $0* | 10K/day (~300K/mo) |
| **Smarty** | $99 (25K plan) | $349 (100K plan) | ~$2,500 | 250/mo |
| **Google AVP** | $0** | $450 | $4,000 | 10K/mo |
| **Lob** | $350 | $2,500 | Contact | None |
| **Melissa** | ~$200 | ~$1,500 | Contact | 1K credits |
| **EasyPost** | $100 | $1,000 | $10,000 | None |

**Notes:**
- *USPS v3 has daily limits (10K/day = 300K/mo theoretical max)
- **Google $200 credit applies across all Maps APIs
- All prices approximate as of Jan 2025; verify current pricing

### 7.2 Feature Comparison

| Feature | USPS v3 | Smarty | Google AVP | Lob |
|---------|---------|--------|------------|-----|
| **CASS Certified** | ✅ Backend | ✅ | ✅ | ✅ |
| **DPV Confirmation** | ✅ | ✅ Full | ✅ | ✅ |
| **DPV Footnotes** | Limited | ✅ Full | ✅ Full | ✅ |
| **Vacancy Flag** | ✅ | ✅ | ✅ | ✅ |
| **RDI** | Partial | ✅ | ❌ | ❌ |
| **LACSLink** | ❌ | ✅ | ❌ | ✅ |
| **SuiteLink** | ❌ | ✅ | ❌ | ✅ |
| **Geocoding** | ❌ | ✅ | ✅ | ❌ |
| **Batch API** | 5 max | 100 max | 1 only | 1 only |
| **Python SDK** | ❌ | ✅ | ✅ | ✅ |
| **International** | ❌ | Separate API | ✅ 40+ | ✅ |
| **Uptime SLA** | None | 99.99% | 99.9% | 99.9% |

### 7.3 Hidden Costs / Gotchas

**USPS v3:**
- No formal SLA
- Rate limits not guaranteed
- Can change terms

**Smarty:**
- Lookups expire monthly (no rollover)
- Minimum plan for production features

**Google:**
- Requires billing account (even for free tier)
- Easy to accidentally use other APIs that cost money

**Lob:**
- Expensive at low volume
- Best if combined with mail services

---

## 8. Recommendations

### 8.1 For Your FastAPI Service

**Recommended approach: USPS v3 Primary + Smarty Fallback**

**Rationale:**
1. USPS v3 is free and authoritative (it's their data)
2. 10K/day limit is sufficient for many applications
3. Smarty provides richer data and higher reliability as fallback
4. Combined cost is manageable

**Architecture:**
```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Service                       │
├─────────────────────────────────────────────────────────┤
│  POST /v1/validate                                       │
│    │                                                     │
│    ├─> Check Redis Cache ─────────────────> HIT ─> Return│
│    │                                                     │
│    ├─> USPS v3 API ──────> Success ──────────────> Cache │
│    │         │                                    + Return│
│    │         └─> Timeout/429 ──┐                        │
│    │                           │                        │
│    └─> Smarty API ─────────────┴──> Success ───> Cache  │
│                                                 + Return│
└─────────────────────────────────────────────────────────┘
```

### 8.2 If You Need More

| Requirement | Recommendation |
|-------------|----------------|
| >300K/mo volume | Smarty as primary |
| RDI is critical | Smarty (USPS doesn't expose) |
| International | Google AVP or Melissa |
| Mail sending too | Lob (validation + printing) |
| Maximum features | Smarty |
| Minimum cost | USPS v3 only (accept limitations) |

### 8.3 Implementation Priority

1. **Phase 3.1:** Implement USPS v3 client with OAuth2
2. **Phase 3.2:** Add Redis caching layer
3. **Phase 3.3:** Add rate limiting
4. **Phase 3.4:** Add Smarty fallback (if needed)
5. **Phase 3.5:** Add monitoring/alerting on validation failures

---

## Appendix A: USPS v3 OAuth2 Example

```python
import httpx
from datetime import datetime, timedelta
from dataclasses import dataclass

@dataclass
class USPSToken:
    access_token: str
    expires_at: datetime

class USPSClient:
    TOKEN_URL = "https://apis.usps.com/oauth2/v3/token"
    ADDRESS_URL = "https://apis.usps.com/addresses/v3/address"
    
    def __init__(self, consumer_key: str, consumer_secret: str, client: httpx.AsyncClient):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.client = client
        self._token: USPSToken | None = None
    
    async def _get_token(self) -> str:
        if self._token and self._token.expires_at > datetime.utcnow():
            return self._token.access_token
        
        response = await self.client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.consumer_key,
                "client_secret": self.consumer_secret,
            }
        )
        response.raise_for_status()
        data = response.json()
        
        self._token = USPSToken(
            access_token=data["access_token"],
            expires_at=datetime.utcnow() + timedelta(seconds=data["expires_in"] - 60)
        )
        return self._token.access_token
    
    async def validate_address(
        self,
        street_address: str,
        city: str,
        state: str,
        zip_code: str = ""
    ) -> dict:
        token = await self._get_token()
        
        response = await self.client.post(
            self.ADDRESS_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "streetAddress": street_address,
                "city": city,
                "state": state,
                "ZIPCode": zip_code,
            }
        )
        response.raise_for_status()
        return response.json()
```

---

## Appendix B: Smarty Direct HTTP Example

```python
import httpx

class SmartyClient:
    BASE_URL = "https://us-street.api.smarty.com/street-address"
    
    def __init__(self, auth_id: str, auth_token: str, client: httpx.AsyncClient):
        self.auth_id = auth_id
        self.auth_token = auth_token
        self.client = client
    
    async def validate_address(
        self,
        street: str,
        city: str,
        state: str,
        zipcode: str = ""
    ) -> dict:
        response = await self.client.get(
            self.BASE_URL,
            params={
                "auth-id": self.auth_id,
                "auth-token": self.auth_token,
                "street": street,
                "city": city,
                "state": state,
                "zipcode": zipcode,
                "match": "strict",  # or "enhanced" or "invalid"
            }
        )
        response.raise_for_status()
        results = response.json()
        return results[0] if results else None
```

---

## Appendix C: Uncertainty Notes

**Items where pricing/availability may have changed:**

1. **USPS v3 rate limits** — USPS has changed these before; verify current limits at developer.usps.com
2. **Smarty pricing** — Tiers may have changed; check smarty.com/pricing
3. **Google pricing** — Generally stable but verify at cloud.google.com/maps-platform/pricing
4. **Lob pricing** — May have introduced new tiers
5. **USPS Web Tools (legacy)** — May be fully deprecated by now

**Verified as of knowledge cutoff (early 2025):**
- USPS v3 API is current
- Smarty SDK version 5.3.0
- Google Address Validation API is GA
- pyusps is abandoned (last update 2018)
