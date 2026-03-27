"""Unit tests for services/training_candidates.py."""

from unittest import mock

import pytest

from address_validator.services.training_candidates import (
    get_candidate_data,
    reset_candidate_data,
    set_candidate_data,
    write_training_candidate,
)


class TestCandidateContextVars:
    def setup_method(self) -> None:
        reset_candidate_data()

    def test_default_is_none(self) -> None:
        assert get_candidate_data() is None

    def test_set_and_get(self) -> None:
        set_candidate_data(
            raw_address="123 Main St",
            failure_type="repeated_label_error",
            parsed_tokens=[("123", "AddressNumber")],
            recovered_components={"address_number": "123"},
        )
        data = get_candidate_data()
        assert data is not None
        assert data["raw_address"] == "123 Main St"
        assert data["failure_type"] == "repeated_label_error"
        assert data["parsed_tokens"] == [("123", "AddressNumber")]
        assert data["recovered_components"] == {"address_number": "123"}

    def test_reset(self) -> None:
        set_candidate_data(
            raw_address="test",
            failure_type="test",
            parsed_tokens=[],
        )
        reset_candidate_data()
        assert get_candidate_data() is None


class TestWriteTrainingCandidate:
    @pytest.mark.asyncio
    async def test_inserts_row_when_engine_available(self) -> None:
        mock_conn = mock.AsyncMock()
        mock_ctx = mock.MagicMock()
        mock_ctx.__aenter__ = mock.AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = mock.AsyncMock(return_value=False)
        mock_engine = mock.MagicMock()
        mock_engine.begin.return_value = mock_ctx

        await write_training_candidate(
            engine=mock_engine,
            raw_address="995 9TH ST BLDG 201",
            failure_type="repeated_label_error",
            parsed_tokens=[("995", "AddressNumber"), ("BLDG", "SubaddressType")],
            recovered_components={"address_number": "995"},
        )

        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self, caplog: pytest.LogCaptureFixture) -> None:
        mock_engine = mock.AsyncMock()
        mock_engine.begin.side_effect = Exception("connection refused")

        await write_training_candidate(
            engine=mock_engine,
            raw_address="test",
            failure_type="repeated_label_error",
            parsed_tokens=[],
        )

        assert "failed to write training candidate" in caplog.text

    @pytest.mark.asyncio
    async def test_none_engine_is_noop(self) -> None:
        await write_training_candidate(
            engine=None,
            raw_address="test",
            failure_type="repeated_label_error",
            parsed_tokens=[],
        )
