"""The CA roots the service's own HTTPS calls trust: certifi's bundle, alone (#420).

httpx2 trusts the OS store by default (truststore), where httpx read certifi.
The service keeps certifi, because the two disagree: on this host the OS store
still trusts emSign C1 and ECC C3, which certifi 2026.10.23 dropped
(CannObserv/provisioner#77; the eMudhra compromise behind #343 and #345). The
lock decides the bundle, and ``tests/ci/test_trust_store.py`` checks it.

Every httpx2 client or request under ``src/`` passes ``verify=trusted()``,
plain-http ones included, so the guard needs no exceptions; the same test file
holds them to it. ``SSL_CERT_FILE`` does not apply: an explicit context
overrides it. The ops scripts under ``infra/`` cannot import this package (the
patching recovery knob runs with no PYTHONPATH), so ``infra/tls.py`` mirrors it,
and the test checks both. notifier is reached over plain ``http://`` on the
tailnet, so the SDK's own client, which cannot be configured, opens no TLS.

Modeled on co-status's ``src/core/tls.py`` (CannObserv/status#125).
"""

import ssl
from functools import cache

import certifi


@cache
def trusted() -> ssl.SSLContext:
    """A verifying client context holding certifi's roots and nothing else.

    One per process: building it parses the whole bundle, and a context is safe
    to share between clients.
    """
    return ssl.create_default_context(cafile=certifi.where())
