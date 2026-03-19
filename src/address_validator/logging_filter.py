"""Logging filter that injects the current request ID into every log record."""

import logging

from address_validator.middleware.request_id import get_request_id


class RequestIdFilter(logging.Filter):
    """Attach ``request_id`` to every :class:`logging.LogRecord`.

    Install on the root logger (or individual loggers) so that any formatter
    using ``%(request_id)s`` can include the ULID correlation ID without
    callers needing to pass it explicitly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()  # type: ignore[attr-defined]
        return True
