"""Notifier config and dispatch shared by the infra alert scripts (#232, #249).

Each script names its own stored template; all of them send through the
``address-validator`` tenant to the unit-failure channels. ``NOTIFIER_URL``
unset means journal only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from notifier_client import RetryConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from notifier_client import NotifierClient

logger = logging.getLogger("notifier_dispatch")

EXPECTED_ENVIRONMENT = "production"
CHANNELS_VAR = "NOTIFIER_UNIT_FAILURE_CHANNEL_IDS"
# Notifier delivers to every channel before it answers the dispatch, so the read
# covers Slack + Mailgun round trips. A read timeout on a delivered dispatch would
# log a false failure and retry into the in-flight request.
REQUEST_TIMEOUT_SECONDS = 30.0
RETRY = RetryConfig(max_attempts=2)


class ConfigError(ValueError):
    """NOTIFIER_URL is set but the rest of the notifier config is not."""


@dataclass(frozen=True)
class NotifierConfig:
    url: str
    api_key: str
    template_id: str
    channel_ids: list[str]


def load_config(env: Mapping[str, str], *, template_var: str) -> NotifierConfig | None:
    """Return the notifier config, or None when NOTIFIER_URL is unset (journal only)."""
    url = env.get("NOTIFIER_URL", "").strip()
    if not url:
        return None
    api_key = env.get("NOTIFIER_API_KEY", "").strip()
    template_id = env.get(template_var, "").strip()
    channel_ids = [c.strip() for c in env.get(CHANNELS_VAR, "").split(",") if c.strip()]
    missing = [
        name
        for name, value in (
            ("NOTIFIER_API_KEY", api_key),
            (template_var, template_id),
            (CHANNELS_VAR, channel_ids),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"NOTIFIER_URL is set but {', '.join(missing)} is not")
    return NotifierConfig(url, api_key, template_id, channel_ids)


async def dispatch(
    config: NotifierConfig,
    *,
    subject: str,
    variables: dict[str, str],
    idempotency_key: str,
    metadata: dict[str, Any],
    client_factory: Callable[..., NotifierClient],
) -> str | None:
    """Dispatch the template; return the dispatch status, or None if nothing was sent.

    ``subject`` names what the dispatch is about in the log lines.
    """
    async with client_factory(
        base_url=config.url,
        api_key=config.api_key,
        retry_config=RETRY,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as client:
        environment = (await client.health()).get("environment")
        if environment != EXPECTED_ENVIRONMENT:
            logger.warning(
                "notifier at NOTIFIER_URL reports environment=%s, expected %s; not dispatching",
                environment,
                EXPECTED_ENVIRONMENT,
            )
            return None
        result = await client.dispatch(
            template_id=config.template_id,
            channel_ids=config.channel_ids,
            variables=variables,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
    # 202 does not mean delivered: judge by the body's status (notifier#70).
    status = str(result.status)
    if status == "succeeded":
        logger.info("notifier dispatch %s for %s: succeeded", result.id, subject)
    else:
        logger.warning(
            "notifier dispatch %s for %s: %s (%d of %d channels delivered)",
            result.id,
            subject,
            status,
            sum(str(a.status) == "succeeded" for a in result.attempts),
            len(result.attempts),
        )
    return status
