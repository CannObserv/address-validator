"""Unit tests for custom usaddress model loading."""

import os
from pathlib import Path
from unittest import mock

import pytest
import usaddress

from address_validator.main import _load_custom_model


class TestLoadCustomModel:
    def test_loads_custom_model_when_path_set(self) -> None:
        """CUSTOM_MODEL_PATH pointing to a valid .crfsuite swaps the tagger."""
        bundled_path = usaddress.MODEL_PATH
        original_tagger = usaddress.TAGGER
        try:
            with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": bundled_path}):
                _load_custom_model()
            # Tagger should have been replaced (even if same model file)
            assert usaddress.TAGGER is not original_tagger
        finally:
            usaddress.TAGGER = original_tagger

    def test_warns_on_missing_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-existent path logs a warning and keeps bundled model."""
        original_tagger = usaddress.TAGGER
        try:
            with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": "/nonexistent/model.crfsuite"}):
                _load_custom_model()
            assert usaddress.TAGGER is original_tagger
            assert "not found" in caplog.text
        finally:
            usaddress.TAGGER = original_tagger

    def test_falls_back_on_corrupt_model(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A path that exists but isn't a valid CRF model logs a warning and keeps bundled model."""
        bad_model = tmp_path / "bad.crfsuite"
        bad_model.write_bytes(b"not a crfsuite model")
        original_tagger = usaddress.TAGGER
        try:
            with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": str(bad_model)}):
                _load_custom_model()
            assert usaddress.TAGGER is original_tagger
            assert "failed to load" in caplog.text
        finally:
            usaddress.TAGGER = original_tagger

    def test_noop_when_env_unset(self) -> None:
        """No CUSTOM_MODEL_PATH means bundled model is used."""
        original_tagger = usaddress.TAGGER
        try:
            env = {k: v for k, v in os.environ.items() if k != "CUSTOM_MODEL_PATH"}
            with mock.patch.dict(os.environ, env, clear=True):
                _load_custom_model()
            assert usaddress.TAGGER is original_tagger
        finally:
            usaddress.TAGGER = original_tagger
