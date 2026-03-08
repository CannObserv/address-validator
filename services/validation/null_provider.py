"""NullProvider — safe no-op backend used when no provider is configured."""

import logging

from models import ValidateRequestV1, ValidateResponseV1

logger = logging.getLogger(__name__)


class NullProvider:
    """Returns ``validation_status='unavailable'`` for every request.

    Used as the default backend so the service starts cleanly without any
    external credentials.  Suitable for development and environments where
    validation is not yet required.
    """

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        logger.debug("NullProvider: returning unavailable for country=%s", request.country)
        return ValidateResponseV1(
            input_address=request.address,
            country=request.country,
            validation_status="unavailable",
            provider=None,
            dpv_match_code=None,
            zip_plus4=None,
            vacant=None,
            corrected_components=None,
        )
