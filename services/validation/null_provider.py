"""NullProvider — safe no-op backend used when no provider is configured."""

import logging

from models import ValidateRequestV1, ValidateResponseV1, ValidationResult

logger = logging.getLogger(__name__)


class NullProvider:
    """Returns ``validation.status='unavailable'`` for every request.

    Used as the default backend so the service starts cleanly without any
    external credentials.  Suitable for development and environments where
    validation is not yet required.
    """

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        logger.debug("NullProvider: returning unavailable for country=%s", request.country)
        return ValidateResponseV1(
            country=request.country,
            validation=ValidationResult(status="unavailable"),
        )
