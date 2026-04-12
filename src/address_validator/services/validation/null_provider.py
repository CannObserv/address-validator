"""NullProvider — safe no-op backend used when no provider is configured."""

import logging

from address_validator.models import StandardizedAddress, ValidateResponseV1, ValidationResult

logger = logging.getLogger(__name__)


class NullProvider:
    """Returns ``validation.status='unavailable'`` for every request.

    Used as the default backend so the service starts cleanly without any
    external credentials.  Suitable for development and environments where
    validation is not yet required.
    """

    supports_non_us = False

    async def validate(
        self, std: StandardizedAddress, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        logger.debug("NullProvider: returning unavailable for country=%s", std.country)
        return ValidateResponseV1(
            country=std.country,
            validation=ValidationResult(status="unavailable"),
        )
