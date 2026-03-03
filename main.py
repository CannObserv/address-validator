"""Address Validator — FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import parse, standardize
from routers.v1 import health as v1_health
from routers.v1 import parse as v1_parse
from routers.v1 import standardize as v1_standardize

_DESCRIPTION = """
Parse and standardize physical addresses.

Version 1 of this API uses geography-neutral field names (`region`,
`postal_code`) and is designed to support multiple countries as
additional parsers are added.  Currently **US addresses only** (USPS
Publication 28).

## Versioning

| Prefix | Status |
|---|---|
| `/api/v1/` | **Current** |
| `/api/` | Deprecated — migrate to v1 |

Deprecated routes return `Deprecation: true` and
`Link: <successor>; rel="successor-version"` response headers.
"""

_TAGS = [
    {
        "name": "v1",
        "description": "Current API — versioned routes under `/api/v1/`.",
    },
    {
        "name": "health",
        "description": "Service liveness probe. No authentication required.",
    },
    {
        "name": "deprecated",
        "description": "Unversioned routes retained for backward compatibility. **Migrate to `/api/v1/`.**",
    },
]

app = FastAPI(
    title="Address Validator API",
    description=_DESCRIPTION,
    version="1.0.0",
    openapi_tags=_TAGS,
    contact={"name": "Cannabis Observer", "email": "greg@cannabis.observer"},
    license_info={"name": "Proprietary"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# v1 routes (current)
app.include_router(v1_health.router)
app.include_router(v1_parse.router)
app.include_router(v1_standardize.router)

# Deprecated unversioned routes
app.include_router(parse.router)
app.include_router(standardize.router)
