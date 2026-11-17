"""The CA roots the ops scripts' HTTPS calls trust: certifi's bundle, alone (#420).

The ``infra/`` twin of ``address_validator.core.tls``, which says why certifi
and not the OS store httpx2 defaults to. It exists because
``patching_recovery_check.py`` runs from the patching knob with no PYTHONPATH
and cannot import the service package; the scripts import this sibling, as they
do ``journal_logging``. ``tests/ci/test_trust_store.py`` holds both to the same
bundle, and every httpx2 call site under ``infra/`` to ``verify=trusted()``.
"""

import ssl
from functools import cache

import certifi


@cache
def trusted() -> ssl.SSLContext:
    """A verifying client context holding certifi's roots and nothing else."""
    return ssl.create_default_context(cafile=certifi.where())
