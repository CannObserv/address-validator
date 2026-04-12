"""ValidationProvider protocol — the interface every backend must satisfy."""

from typing import Protocol, runtime_checkable

from address_validator.models import StandardizedAddress, ValidateResponseV1


@runtime_checkable
class ValidationProvider(Protocol):
    """Async interface for address-validation backends.

    All providers receive a fully normalised :class:`~models.StandardizedAddress`
    (the result of the parse → standardize pipeline) rather than raw user input.
    The router owns normalisation; providers own validation only.

    Concrete implementations: :class:`~services.validation.null_provider.NullProvider`,
    :class:`~services.validation.usps_provider.USPSProvider`,
    :class:`~services.validation.google_provider.GoogleProvider`.
    """

    supports_non_us: bool
    """True if this provider can validate non-US addresses."""

    async def validate(
        self, std: StandardizedAddress, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        """Validate the standardised address *std* and return an authoritative response.

        Args:
            std: Fully normalised address from the parse → standardize pipeline.
            raw_input: If provided, carries the original pre-parse caller string for
                providers that store it; most providers accept and ignore it.
        """
        ...
