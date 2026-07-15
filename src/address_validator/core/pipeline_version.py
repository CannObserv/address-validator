"""Pipeline version stamp — single source of truth for cache invalidation (#145).

The composite version ``{PIPELINE_CODE_VERSION}+{model_fingerprint}`` identifies
the parse → standardize pipeline that produced a cached validation result.
``validated_addresses.pipeline_version`` rows stamped with a different value are
treated as cache misses and lazily re-validated; the #144 TTL sweeper reaps them.

Two change vectors, two segments:

- ``PIPELINE_CODE_VERSION`` — hand-bumped integer. Bump it whenever a code change
  alters parse/standardize *output* (parser cleanup regexes, standardizer rules,
  street splitting, component mapping). The golden-corpus drift test
  (``tests/unit/test_pipeline_output_pin.py``) fails when pipeline output changes
  without a bump — update both together.
- model fingerprint — sha256 prefix of the loaded ``.crfsuite`` file, recorded by
  :func:`load_custom_model`; ``bundled-<usaddress dist version>`` when no custom
  model is active. CRF retrains deployed via ``CUSTOM_MODEL_PATH`` therefore
  invalidate automatically, with no code change or manual bump.

Known gap: the CA/libpostal sidecar is not fingerprinted — a libpostal image
upgrade that changes CA parses is invisible to this stamp (bump
``PIPELINE_CODE_VERSION`` manually when upgrading the sidecar).
"""

import hashlib
import logging
import os
from functools import cache
from importlib.metadata import version as _dist_version
from pathlib import Path

logger = logging.getLogger(__name__)

# Bump on any code change that alters parse/standardize output. See module docstring.
PIPELINE_CODE_VERSION = 4

# Fingerprint of the active custom model; None → bundled usaddress model.
_model_fingerprint: str | None = None


def fingerprint_model_file(path: str | Path) -> str:
    """Return a short (12 hex chars) sha256 fingerprint of a model file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


@cache
def _bundled_fingerprint() -> str:
    return f"bundled-{_dist_version('usaddress')}"


def get_pipeline_version() -> str:
    """Return the composite pipeline version for stamping/matching cache rows."""
    return f"{PIPELINE_CODE_VERSION}+{_model_fingerprint or _bundled_fingerprint()}"


def reset_model_fingerprint() -> None:
    """Revert to the bundled-model fingerprint (test isolation helper)."""
    global _model_fingerprint  # noqa: PLW0603
    _model_fingerprint = None


def load_custom_model() -> None:
    """Swap usaddress.TAGGER with a custom .crfsuite model if configured.

    Reads CUSTOM_MODEL_PATH from environment. No-op when unset. Logs a warning
    and falls back to the bundled model if the path is invalid. On success,
    records the model fingerprint so :func:`get_pipeline_version` reflects the
    model actually serving parses — every fallback path leaves the bundled
    fingerprint in place so the stamp never claims a model that isn't loaded.
    """
    import pycrfsuite  # noqa: PLC0415
    import usaddress  # noqa: PLC0415

    global _model_fingerprint  # noqa: PLW0603
    _model_fingerprint = None

    custom_path = os.environ.get("CUSTOM_MODEL_PATH", "").strip()
    if not custom_path:
        return

    path = Path(custom_path)
    if not path.exists():
        logger.warning("CUSTOM_MODEL_PATH=%s not found, using bundled model", path)
        return

    try:
        tagger = pycrfsuite.Tagger()
        tagger.open(str(path))
        usaddress.TAGGER = tagger
        _model_fingerprint = fingerprint_model_file(path)
        logger.info("loaded custom usaddress model: %s (fingerprint %s)", path, _model_fingerprint)
    except Exception:
        logger.warning(
            "CUSTOM_MODEL_PATH=%s failed to load, using bundled model", path, exc_info=True
        )
