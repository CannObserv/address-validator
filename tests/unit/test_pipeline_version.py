"""Unit tests for core.pipeline_version — the cache invalidation version stamp (#145)."""

import os
import re
from importlib.metadata import version as dist_version
from pathlib import Path
from unittest import mock

import pytest
import usaddress

from address_validator.core.pipeline_version import (
    PIPELINE_CODE_VERSION,
    fingerprint_model_file,
    get_pipeline_version,
    load_custom_model,
    reset_model_fingerprint,
)


@pytest.fixture(autouse=True)
def _reset_fingerprint():
    """Isolate module-level fingerprint state between tests."""
    reset_model_fingerprint()
    yield
    reset_model_fingerprint()


@pytest.fixture()
def _restore_tagger():
    """Restore usaddress.TAGGER after tests that swap it."""
    original = usaddress.TAGGER
    yield
    usaddress.TAGGER = original


class TestPipelineCodeVersion:
    def test_is_positive_int(self) -> None:
        assert isinstance(PIPELINE_CODE_VERSION, int)
        assert PIPELINE_CODE_VERSION >= 1


class TestFingerprintModelFile:
    def test_is_short_hex(self, tmp_path: Path) -> None:
        f = tmp_path / "m.crfsuite"
        f.write_bytes(b"model-bytes")
        assert re.fullmatch(r"[0-9a-f]{12}", fingerprint_model_file(f))

    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "m.crfsuite"
        f.write_bytes(b"model-bytes")
        assert fingerprint_model_file(f) == fingerprint_model_file(f)

    def test_differs_by_content(self, tmp_path: Path) -> None:
        a = tmp_path / "a.crfsuite"
        b = tmp_path / "b.crfsuite"
        a.write_bytes(b"model-a")
        b.write_bytes(b"model-b")
        assert fingerprint_model_file(a) != fingerprint_model_file(b)


class TestGetPipelineVersion:
    def test_bundled_default_format(self) -> None:
        """Without a custom model, the version is <code>+bundled-<usaddress dist version>."""
        expected = f"{PIPELINE_CODE_VERSION}+bundled-{dist_version('usaddress')}"
        assert get_pipeline_version() == expected

    def test_custom_model_fingerprint(self, _restore_tagger: None) -> None:
        """A successfully loaded custom model swaps the fingerprint segment."""
        bundled_path = usaddress.MODEL_PATH
        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": bundled_path}):
            load_custom_model()
        expected_fp = fingerprint_model_file(bundled_path)
        assert get_pipeline_version() == f"{PIPELINE_CODE_VERSION}+{expected_fp}"

    def test_reset_restores_bundled(self, _restore_tagger: None) -> None:
        bundled_path = usaddress.MODEL_PATH
        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": bundled_path}):
            load_custom_model()
        reset_model_fingerprint()
        assert "bundled-" in get_pipeline_version()


class TestLoadCustomModel:
    """Behavior moved from main._load_custom_model — fallback semantics preserved."""

    def test_loads_custom_model_when_path_set(self, _restore_tagger: None) -> None:
        original_tagger = usaddress.TAGGER
        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": usaddress.MODEL_PATH}):
            load_custom_model()
        assert usaddress.TAGGER is not original_tagger

    def test_missing_path_keeps_bundled_fingerprint(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-existent path: bundled model stays loaded AND version stays bundled."""
        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": "/nonexistent/model.crfsuite"}):
            load_custom_model()
        assert "not found" in caplog.text
        assert "bundled-" in get_pipeline_version()

    def test_corrupt_model_keeps_bundled_fingerprint(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Corrupt file: fallback to bundled model must NOT fingerprint the bad file."""
        bad = tmp_path / "bad.crfsuite"
        bad.write_bytes(b"not a crfsuite model")
        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": str(bad)}):
            load_custom_model()
        assert "failed to load" in caplog.text
        assert "bundled-" in get_pipeline_version()

    def test_unset_env_is_noop(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CUSTOM_MODEL_PATH"}
        original_tagger = usaddress.TAGGER
        with mock.patch.dict(os.environ, env, clear=True):
            load_custom_model()
        assert usaddress.TAGGER is original_tagger
        assert "bundled-" in get_pipeline_version()

    def test_reload_after_custom_clears_stale_fingerprint(
        self, tmp_path: Path, _restore_tagger: None
    ) -> None:
        """Loading custom then failing a reload must not leave the old fingerprint."""
        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": usaddress.MODEL_PATH}):
            load_custom_model()
        assert "bundled-" not in get_pipeline_version()

        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": "/nonexistent/model.crfsuite"}):
            load_custom_model()
        assert "bundled-" in get_pipeline_version()
