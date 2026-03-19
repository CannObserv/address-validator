"""ValidationProvider protocol — the interface every backend must satisfy."""

from typing import Protocol, runtime_checkable

from address_validator.models import StandardizeResponseV1, ValidateResponseV1


@runtime_checkable
class ValidationProvider(Protocol):
    """Async interface for address-validation backends.

    All providers receive a fully normalised :class:`~models.StandardizeResponseV1`
    (the result of the parse → standardize pipeline) rather than raw user input.
    The router owns normalisation; providers own validation only.

    Concrete implementations: :class:`~services.validation.null_provider.NullProvider`,
    :class:`~services.validation.usps_provider.USPSProvider`,
    :class:`~services.validation.google_provider.GoogleProvider`.
    """

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        """Validate the standardised address *std* and return an authoritative response."""
        ...
