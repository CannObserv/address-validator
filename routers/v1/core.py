"""Shared constants, exceptions, and utilities for v1 route handlers."""

from fastapi import status
from fastapi.responses import JSONResponse

from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION  # re-exported for convenience

# ---------------------------------------------------------------------------
# Country validation
# ---------------------------------------------------------------------------

# ISO 3166-1 alpha-2 codes currently supported by this service.
# Extend as non-US parsing is added in future versions.
SUPPORTED_COUNTRIES: frozenset[str] = frozenset({"US"})

# Full set of valid ISO 3166-1 alpha-2 codes (static; avoids a heavy
# dependency).  Source: https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2
VALID_ISO2: frozenset[str] = frozenset({
    "AD","AE","AF","AG","AI","AL","AM","AO","AQ","AR","AS","AT","AU","AW",
    "AX","AZ","BA","BB","BD","BE","BF","BG","BH","BI","BJ","BL","BM","BN",
    "BO","BQ","BR","BS","BT","BV","BW","BY","BZ","CA","CC","CD","CF","CG",
    "CH","CI","CK","CL","CM","CN","CO","CR","CU","CV","CW","CX","CY","CZ",
    "DE","DJ","DK","DM","DO","DZ","EC","EE","EG","EH","ER","ES","ET","FI",
    "FJ","FK","FM","FO","FR","GA","GB","GD","GE","GF","GG","GH","GI","GL",
    "GM","GN","GP","GQ","GR","GS","GT","GU","GW","GY","HK","HM","HN","HR",
    "HT","HU","ID","IE","IL","IM","IN","IO","IQ","IR","IS","IT","JE","JM",
    "JO","JP","KE","KG","KH","KI","KM","KN","KP","KR","KW","KY","KZ","LA",
    "LB","LC","LI","LK","LR","LS","LT","LU","LV","LY","MA","MC","MD","ME",
    "MF","MG","MH","MK","ML","MM","MN","MO","MP","MQ","MR","MS","MT","MU",
    "MV","MW","MX","MY","MZ","NA","NC","NE","NF","NG","NI","NL","NO","NP",
    "NR","NU","NZ","OM","PA","PE","PF","PG","PH","PK","PL","PM","PN","PR",
    "PS","PT","PW","PY","QA","RE","RO","RS","RU","RW","SA","SB","SC","SD",
    "SE","SG","SH","SI","SJ","SK","SL","SM","SN","SO","SR","SS","ST","SV",
    "SX","SY","SZ","TC","TD","TF","TG","TH","TJ","TK","TL","TM","TN","TO",
    "TR","TT","TV","TW","TZ","UA","UG","UM","US","UY","UZ","VA","VC","VE",
    "VG","VI","VN","VU","WF","WS","YE","YT","ZA","ZM","ZW",
})

# ---------------------------------------------------------------------------
# Structured API errors
# ---------------------------------------------------------------------------

class APIError(Exception):
    """Raised by v1 route handlers to produce a structured ErrorResponse body.

    Caught by the ``api_error_handler`` registered in ``main.py``, which
    serialises it directly as the response body (no ``{"detail": ...}``
    wrapping).  The ``API-Version`` header is added by middleware.
    """

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message


def api_error_response(exc: "APIError") -> JSONResponse:
    """Serialise *exc* to a :class:`JSONResponse` with the correct status code.

    Called from the exception handler registered in ``main.py``.
    Importing ``ErrorResponse`` here would create a circular dependency
    (models → core is fine; core → models is fine; but the handler needs
    both), so the payload is built as a plain dict mirroring
    ``ErrorResponse``'s fields.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error,
            "message": exc.message,
            "api_version": "1",
        },
    )


def check_country(country: str) -> None:
    """Validate *country* against VALID_ISO2 and SUPPORTED_COUNTRIES.

    Raises :class:`APIError` with an appropriate status code and
    machine-readable error code if the value is invalid or unsupported.
    Does nothing when the country is valid and supported.
    """
    if country not in VALID_ISO2:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error="invalid_country_code",
            message=f"'{country}' is not a valid ISO 3166-1 alpha-2 country code.",
        )
    if country not in SUPPORTED_COUNTRIES:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error="country_not_supported",
            message=f"Country '{country}' is not yet supported. Currently supported: US.",
        )
