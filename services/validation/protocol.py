"""ValidationProvider protocol — the interface every backend must satisfy."""

from typing import Protocol, runtime_checkable

from models import ValidateRequestV1, ValidateResponseV1


@runtime_checkable
class ValidationProvider(Protocol):
    """Async interface for address-validation backends.

    Concrete implementations: :class:`~services.validation.null_provider.NullProvider`,
    :class:`~services.validation.usps_provider.USPSProvider`,
    :class:`~services.validation.google_provider.GoogleProvider`.
    """

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        """Validate *request* and return an authoritative response."""
        ...
