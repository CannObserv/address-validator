"""Unit tests for GCP ADC credential loading and project ID resolution."""

from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import DefaultCredentialsError

from address_validator.services.validation.gcp_auth import get_credentials, resolve_project_id


class TestGetCredentials:
    @patch("address_validator.services.validation.gcp_auth.google.auth.default")
    def test_returns_credentials_and_project(self, mock_default) -> None:
        mock_creds = MagicMock()
        mock_default.return_value = (mock_creds, "my-project")
        creds, project = get_credentials()
        assert creds is mock_creds
        assert project == "my-project"

    @patch("address_validator.services.validation.gcp_auth.google.auth.default")
    def test_requests_cloud_platform_scope(self, mock_default) -> None:
        mock_default.return_value = (MagicMock(), "proj")
        get_credentials()
        mock_default.assert_called_once_with(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

    @patch("address_validator.services.validation.gcp_auth.google.auth.default")
    def test_propagates_auth_error(self, mock_default) -> None:
        mock_default.side_effect = DefaultCredentialsError("no creds")
        with pytest.raises(DefaultCredentialsError):
            get_credentials()


class TestResolveProjectId:
    def test_env_var_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "env-project")
        result = resolve_project_id(adc_project="adc-project")
        assert result == "env-project"

    def test_falls_back_to_adc_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        result = resolve_project_id(adc_project="adc-project")
        assert result == "adc-project"

    def test_returns_none_when_neither_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        result = resolve_project_id(adc_project=None)
        assert result is None

    def test_strips_whitespace_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "  my-project  ")
        result = resolve_project_id(adc_project=None)
        assert result == "my-project"

    def test_empty_env_var_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "  ")
        result = resolve_project_id(adc_project="adc-project")
        assert result == "adc-project"
