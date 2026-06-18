"""Unit tests for the USPS v3 client (token caching, request shape, response mapping)."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from address_validator.services.validation._rate_limit import _RETRY_MAX, QuotaGuard, QuotaWindow
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderTransientError,
)
from address_validator.services.validation.usps_client import (
    USPSClient,
    USPSToken,
    _normalise_flag,
    _summarise_shape,
)

TOKEN_RESPONSE = {
    "access_token": "tok-abc",
    "token_type": "Bearer",
    "expires_in": 3600,
}

VALID_ADDRESS_RESPONSE = {
    "address": {
        "streetAddress": "123 MAIN ST",
        "city": "SPRINGFIELD",
        "state": "IL",
        "ZIPCode": "62701",
        "ZIPPlus4": "1234",
    },
    "additionalInfo": {
        "DPVConfirmation": "Y",
        "vacant": "N",
        "business": "N",
        "carrierRoute": "C001",
        "deliveryPoint": "23",
        "DPVCMRA": "N",
    },
}


class TestUSPSToken:
    def test_not_expired_when_fresh(self) -> None:
        token = USPSToken(
            access_token="x",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
        assert not token.is_expired()

    def test_expired_when_in_past(self) -> None:
        token = USPSToken(
            access_token="x",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        assert token.is_expired()


class TestUSPSClient:
    @pytest.fixture()
    def mock_http(self) -> AsyncMock:
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def _default_guard(self) -> QuotaGuard:
        return QuotaGuard(
            windows=[QuotaWindow(limit=5, duration_s=1.0, mode="soft")],
            latency_budget_s=1.0,
            provider_name="usps",
        )

    @pytest.fixture()
    def client(self, mock_http: AsyncMock, _default_guard: QuotaGuard) -> USPSClient:
        return USPSClient(
            consumer_key="key",
            consumer_secret="secret",
            http_client=mock_http,
            quota_guard=_default_guard,
        )

    def _make_response(self, json_data: dict, status_code: int = 200) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_fetches_token_on_first_call(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address(
            street_address="123 Main St",
            city="Springfield",
            state="IL",
        )
        assert mock_http.post.call_count == 1  # token fetch
        assert mock_http.get.call_count == 1  # address call

    @pytest.mark.asyncio
    async def test_reuses_cached_token(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")
        await client.validate_address("456 Oak Ave", "Chicago", "IL")

        assert mock_http.post.call_count == 1  # only one token fetch
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_refreshes_expired_token(self, client: USPSClient, mock_http: AsyncMock) -> None:
        expired = USPSToken(
            access_token="old",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        client._token = expired

        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")
        assert mock_http.post.call_count == 1  # refreshed

    @pytest.mark.asyncio
    async def test_maps_dpv_confirmation(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["dpv_match_code"] == "Y"

    @pytest.mark.asyncio
    async def test_maps_flat_address_fields(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["address_line_1"] == "123 MAIN ST"
        assert result["city"] == "SPRINGFIELD"
        assert result["region"] == "IL"
        assert result["postal_code"] == "62701-1234"
        assert result["vacant"] == "N"
        assert result["dpv_match_code"] == "Y"
        assert "corrected_components" not in result
        assert "zip_plus4" not in result

    @pytest.mark.asyncio
    async def test_concurrent_requests_fetch_token_once(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """Concurrent calls on a cold client must issue exactly one token fetch.

        Verifies the _token_lock prevents the check-then-act race where
        multiple coroutines see an empty/expired token and all race to
        refresh it.
        """
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        results = await asyncio.gather(
            client.validate_address("123 Main St", "Springfield", "IL"),
            client.validate_address("456 Oak Ave", "Chicago", "IL"),
            client.validate_address("789 Pine Rd", "Peoria", "IL"),
        )
        assert len(results) == 3
        assert mock_http.post.call_count == 1  # single token fetch despite 3 concurrent calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_5xx_raises_transient_error(
        self, status: int, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """GH-115: USPS 5xx must map to ProviderTransientError so the chain
        falls through, mirroring the 429 path."""
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = status
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=bad_resp
        )
        mock_http.get.return_value = bad_resp

        with pytest.raises(ProviderTransientError) as exc_info:
            await client.validate_address("999 Fake St", "Nowhere", "IL")
        assert exc_info.value.provider == "usps"
        assert exc_info.value.retry_after_seconds > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_status_raises_bad_request(
        self, status: int, client: USPSClient, mock_http: AsyncMock, caplog
    ) -> None:
        """GH-115: 401/403 from USPS (bad OAuth creds) maps to
        ProviderBadRequestError + ERROR log so operators get paged."""
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = status
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=bad_resp
        )
        mock_http.get.return_value = bad_resp

        with caplog.at_level("ERROR"), pytest.raises(ProviderBadRequestError) as exc_info:
            await client.validate_address("123 Main St", "Springfield", "IL")
        assert exc_info.value.provider == "usps"
        assert str(status) in exc_info.value.detail
        assert any(
            "operator action required" in record.message.lower() and record.levelname == "ERROR"
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_unexpected_status_raises_transient_error(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """GH-115: never leak raw httpx.HTTPStatusError for unhandled status codes."""
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 418
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "418", request=MagicMock(), response=bad_resp
        )
        mock_http.get.return_value = bad_resp

        with pytest.raises(ProviderTransientError):
            await client.validate_address("123 Main St", "Springfield", "IL")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 503])
    async def test_token_endpoint_5xx_raises_transient_error(
        self, status: int, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """GH-115: 5xx from the OAuth2 token endpoint must also map cleanly —
        previously this propagated as raw HTTPStatusError through _get_token."""
        token_fail = MagicMock(spec=httpx.Response)
        token_fail.status_code = status
        token_fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=token_fail
        )
        mock_http.post.return_value = token_fail

        with pytest.raises(ProviderTransientError) as exc_info:
            await client.validate_address("123 Main St", "Springfield", "IL")
        assert exc_info.value.provider == "usps"
        # Address endpoint must NOT have been hit when token fetch failed.
        mock_http.get.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_token_endpoint_auth_failure_raises_bad_request(
        self, status: int, client: USPSClient, mock_http: AsyncMock, caplog
    ) -> None:
        """Token endpoint 401/403 (wrong consumer_key/secret) follows the same
        operator-action path as the address endpoint."""
        token_fail = MagicMock(spec=httpx.Response)
        token_fail.status_code = status
        token_fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=token_fail
        )
        mock_http.post.return_value = token_fail

        with caplog.at_level("ERROR"), pytest.raises(ProviderBadRequestError) as exc_info:
            await client.validate_address("123 Main St", "Springfield", "IL")
        assert exc_info.value.provider == "usps"
        mock_http.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_400_raises_provider_bad_request_error(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 400
        bad_resp.text = "Invalid address"
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=bad_resp
        )
        mock_http.get.return_value = bad_resp

        with pytest.raises(ProviderBadRequestError) as exc_info:
            await client.validate_address("", "Nowhere", "IL")
        assert exc_info.value.provider == "usps"

    @pytest.mark.asyncio
    async def test_zip_plus4_stripped_to_zip5(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL", zip_code="62701-1234")

        _, kwargs = mock_http.get.call_args
        assert kwargs["params"]["ZIPCode"] == "62701"

    @pytest.mark.asyncio
    async def test_secondary_address_sent_as_param(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """GH #126: secondary_address must be sent as the secondaryAddress param."""
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address(
            "9 BENNY DR", "OKANOGAN", "WA", zip_code="98840", secondary_address="LOT B"
        )

        _, kwargs = mock_http.get.call_args
        assert kwargs["params"]["secondaryAddress"] == "LOT B"

    @pytest.mark.asyncio
    async def test_secondary_address_omitted_when_absent(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")

        _, kwargs = mock_http.get.call_args
        assert "secondaryAddress" not in kwargs["params"]

    @pytest.mark.asyncio
    async def test_zip5_passed_unchanged(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL", zip_code="62701")

        _, kwargs = mock_http.get.call_args
        assert kwargs["params"]["ZIPCode"] == "62701"

    @pytest.mark.asyncio
    async def test_429_raises_provider_rate_limited_error_after_retries(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 429
        bad_resp.headers = {}
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=bad_resp
        )
        mock_http.get.return_value = bad_resp

        with (
            patch("address_validator.services.validation.usps_client.asyncio.sleep"),
            pytest.raises(ProviderRateLimitedError) as exc_info,
        ):
            await client.validate_address("123 Main St", "Springfield", "IL")
        assert exc_info.value.provider == "usps"
        assert exc_info.value.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_429_retries_before_giving_up(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 429
        bad_resp.headers = {}
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=bad_resp
        )
        mock_http.get.return_value = bad_resp

        with (
            patch("address_validator.services.validation.usps_client.asyncio.sleep"),
            pytest.raises(ProviderRateLimitedError),
        ):
            await client.validate_address("123 Main St", "Springfield", "IL")

        # _RETRY_MAX retries + 1 initial attempt
        assert mock_http.get.call_count == _RETRY_MAX + 1

    @pytest.mark.asyncio
    async def test_429_then_success_returns_result(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 429
        bad_resp.headers = {}
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=bad_resp
        )
        good_resp = self._make_response(VALID_ADDRESS_RESPONSE)

        mock_http.get.side_effect = [bad_resp, good_resp]

        with patch("address_validator.services.validation.usps_client.asyncio.sleep"):
            result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["dpv_match_code"] == "Y"

    def test_accepts_quota_guard(self, mock_http: AsyncMock) -> None:
        guard = QuotaGuard(
            windows=[QuotaWindow(limit=10, duration_s=1.0, mode="soft")],
            provider_name="usps",
        )
        client = USPSClient(
            consumer_key="key",
            consumer_secret="secret",
            http_client=mock_http,
            quota_guard=guard,
        )
        assert client._rate_limiter is guard

    @pytest.mark.asyncio
    async def test_at_capacity_raises_before_http_call(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """QuotaGuard raising ProviderAtCapacityError must prevent any HTTP call."""
        with (
            patch.object(
                client._rate_limiter,
                "acquire",
                side_effect=ProviderAtCapacityError("usps"),
            ),
            pytest.raises(ProviderAtCapacityError),
        ):
            await client.validate_address("123 Main St", "Springfield", "IL")

        mock_http.get.assert_not_called()
        mock_http.post.assert_not_called()


class TestNormaliseFlag:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("", None),
            (" ", None),
            ("  ", None),
            ("\t", None),
            ("\n", None),
            ("\r\n", None),
            (" \t \n ", None),
            ("Y", "Y"),
            ("S", "S"),
            ("D", "D"),
            ("N", "N"),
            (" Y", "Y"),
            ("Y ", "Y"),
            (" Y ", "Y"),
            ("\tY\n", "Y"),
        ],
    )
    def test_normalise_flag(self, value: str | None, expected: str | None) -> None:
        assert _normalise_flag(value) == expected


class TestMapResponse:
    def test_map_response_merges_zip_plus4(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "city": "SPRINGFIELD",
                "state": "IL",
                "ZIPCode": "62701",
                "ZIPPlus4": "1234",
            },
            "additionalInfo": {"DPVConfirmation": "Y", "vacant": "N"},
        }
        result = USPSClient._map_response(raw)
        assert result["postal_code"] == "62701-1234"

    def test_map_response_without_zip_plus4(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "city": "SPRINGFIELD",
                "state": "IL",
                "ZIPCode": "62701",
            },
            "additionalInfo": {"DPVConfirmation": "Y", "vacant": "N"},
        }
        result = USPSClient._map_response(raw)
        assert result["postal_code"] == "62701"

    def test_map_response_secondary_address(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "secondaryAddress": "APT 4",
                "city": "SPRINGFIELD",
                "state": "IL",
                "ZIPCode": "62701",
            },
            "additionalInfo": {"DPVConfirmation": "S"},
        }
        result = USPSClient._map_response(raw)
        assert result["address_line_2"] == "APT 4"

    def test_map_response_vacant_surfaced(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "city": "X",
                "state": "IL",
                "ZIPCode": "62701",
            },
            "additionalInfo": {"DPVConfirmation": "Y", "vacant": "Y"},
        }
        result = USPSClient._map_response(raw)
        assert result["vacant"] == "Y"

    def test_map_response_no_street_returns_empty_address_line_1(self) -> None:
        raw = {"address": {}, "additionalInfo": {"DPVConfirmation": "N"}}
        result = USPSClient._map_response(raw)
        assert result["address_line_1"] == ""

    def test_map_response_blank_dpv_confirmation_normalised_to_none(self) -> None:
        # USPS returns DPVConfirmation=" " when it can't reach a DPV
        # determination (e.g. commercial buildings without individual
        # delivery-point records). Real payload captured from production.
        raw = {
            "address": {
                "streetAddress": "1319 E METHOW VALLEY HWY",
                "city": "TWISP",
                "state": "WA",
                "ZIPCode": "98856",
            },
            "additionalInfo": {
                "deliveryPoint": "",
                "carrierRoute": "",
                "DPVConfirmation": " ",
                "DPVCMRA": "",
                "business": "",
                "centralDeliveryPoint": "",
                "vacant": "",
            },
        }
        result = USPSClient._map_response(raw)
        assert result["dpv_match_code"] is None
        assert result["vacant"] is None

    @pytest.mark.parametrize("whitespace", [" ", "  ", "\t", "\n", "\r\n", " \t "])
    def test_map_response_whitespace_dpv_variants_collapse_to_none(self, whitespace: str) -> None:
        raw = {
            "address": {"streetAddress": "X", "ZIPCode": "00000"},
            "additionalInfo": {"DPVConfirmation": whitespace, "vacant": whitespace},
        }
        result = USPSClient._map_response(raw)
        assert result["dpv_match_code"] is None
        assert result["vacant"] is None


class TestSummariseShape:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "none"),
            (True, "bool"),
            ("foo", "str"),
            (1, "int"),
            (1.5, "float"),
            ([], "list[empty]"),
        ],
    )
    def test_summarise_scalar_and_empty(self, value: object, expected: str) -> None:
        assert _summarise_shape(value) == expected

    def test_summarise_dict_emits_sorted_keys_only(self) -> None:
        # Values are dropped — only key names reach the log.
        out = _summarise_shape({"name": "ACME LLC", "phone": "555-1212"})
        assert out == "dict[keys=['name', 'phone']]"
        assert "ACME" not in out
        assert "555" not in out

    def test_summarise_list_carries_length_bucket_and_item_shape(self) -> None:
        out = _summarise_shape(
            [
                {"code": "SENTINELVALUE1", "text": "SECRETZIP"},
                {"code": "SENTINELVALUE2", "text": "OTHERSECRET"},
            ]
        )
        # len=2 buckets to "many" so the dedup set doesn't grow per-length.
        assert out == "list[len=many,item=dict[keys=['code', 'text']]]"
        # Item values must not leak into the summary.
        assert "SENTINELVALUE1" not in out
        assert "SECRETZIP" not in out
        assert "OTHERSECRET" not in out

    @pytest.mark.parametrize(
        ("length", "expected_bucket"),
        [
            (1, "len=1"),
            (2, "len=many"),
            (5, "len=many"),
            (100, "len=many"),
        ],
    )
    def test_summarise_list_length_buckets(self, length: int, expected_bucket: str) -> None:
        # All lengths >= 2 collapse to "many" so a single signature covers
        # the "this field is a non-trivial list" case across process lifetime.
        out = _summarise_shape([{"k": "v"}] * length)
        assert expected_bucket in out


class TestReconLogging:
    """Issue #122 — recon logging of unsurfaced USPS top-level fields."""

    @pytest.fixture(autouse=True)
    def _isolate_recon_state(self) -> Generator[None, None, None]:
        USPSClient._reset_recon_state()
        yield
        USPSClient._reset_recon_state()

    def test_no_log_when_only_consumed_keys_present(self, caplog) -> None:
        raw = {
            "address": {"streetAddress": "X", "ZIPCode": "00000"},
            "additionalInfo": {"DPVConfirmation": "Y"},
        }
        with caplog.at_level("INFO", logger="address_validator.services.validation.usps_client"):
            USPSClient._map_response(raw)
        recon_records = [r for r in caplog.records if "recon" in r.getMessage()]
        assert recon_records == []

    def test_logs_shape_of_discarded_fields_at_info(self, caplog) -> None:
        # Mirrors the partial-match payload that motivated issue #122.
        raw = {
            "address": {"streetAddress": "X", "ZIPCode": "00000"},
            "additionalInfo": {"DPVConfirmation": " "},
            "corrections": [{"code": "A", "text": "expanded zip"}],
            "firm": {"name": "ACME LLC"},
            "matches": [{"address": {}, "score": 0.9}],
        }
        with caplog.at_level("INFO", logger="address_validator.services.validation.usps_client"):
            USPSClient._map_response(raw)
        recon = [r for r in caplog.records if "recon" in r.getMessage()]
        assert len(recon) == 1
        msg = recon[0].getMessage()
        # Cross-reference coordinate (normalised DPV) is in the line.
        assert "dpv=None" in msg
        # Structural metadata for each unsurfaced key is present.
        for key in ("corrections", "firm", "matches"):
            assert key in msg
        # Values must not leak — neither street content nor firm name.
        assert "ACME" not in msg
        assert "expanded zip" not in msg

    def test_dedup_suppresses_repeat_signatures(self, caplog) -> None:
        raw = {
            "address": {"streetAddress": "X", "ZIPCode": "00000"},
            "additionalInfo": {"DPVConfirmation": "Y"},
            "firm": {"name": "ACME"},
        }
        with caplog.at_level("INFO", logger="address_validator.services.validation.usps_client"):
            USPSClient._map_response(raw)
            USPSClient._map_response(raw)
            USPSClient._map_response(raw)
        recon = [r for r in caplog.records if "recon" in r.getMessage()]
        assert len(recon) == 1

    def test_different_dpv_codes_are_separate_signatures(self, caplog) -> None:
        # Same structural shape across DPV codes should still log once per
        # code — that's the matrix coordinate we want to populate.
        base = {
            "address": {"streetAddress": "X", "ZIPCode": "00000"},
            "firm": {"name": "ACME"},
        }
        with caplog.at_level("INFO", logger="address_validator.services.validation.usps_client"):
            for dpv in ("Y", "S", "D", "N"):
                USPSClient._map_response({**base, "additionalInfo": {"DPVConfirmation": dpv}})
        recon = [r for r in caplog.records if "recon" in r.getMessage()]
        assert len(recon) == 4
        observed_codes = {f"dpv={code}" for code in ("Y", "S", "D", "N")}
        seen = {code for code in observed_codes if any(code in r.getMessage() for r in recon)}
        assert seen == observed_codes

    def test_truly_unknown_top_level_keys_are_logged(self, caplog) -> None:
        # A new USPS field we've never seen — recon should pick it up.
        raw = {
            "address": {"streetAddress": "X", "ZIPCode": "00000"},
            "additionalInfo": {"DPVConfirmation": "Y"},
            "futureUSPSField": {"foo": 1, "bar": 2},
        }
        with caplog.at_level("INFO", logger="address_validator.services.validation.usps_client"):
            USPSClient._map_response(raw)
        recon = [r for r in caplog.records if "recon" in r.getMessage()]
        assert len(recon) == 1
        assert "futureUSPSField" in recon[0].getMessage()
