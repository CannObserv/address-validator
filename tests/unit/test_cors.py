"""CORS policy tests (GH #35).

The app defaults to NO cross-origin access: ``ALLOWED_ORIGINS`` unset means
CORSMiddleware is configured with an empty origin list, so no
``Access-Control-Allow-Origin`` header is ever emitted. Browser consumers must
be granted explicitly via the env var (comma-separated origins, or ``*``).
"""

from starlette.testclient import TestClient

from address_validator.main import _parse_allowed_origins


class TestParseAllowedOrigins:
    def test_unset_yields_no_origins(self) -> None:
        assert _parse_allowed_origins("") == []

    def test_whitespace_only_yields_no_origins(self) -> None:
        assert _parse_allowed_origins("  ") == []

    def test_single_origin(self) -> None:
        assert _parse_allowed_origins("https://app.example.com") == ["https://app.example.com"]

    def test_comma_separated_origins_stripped(self) -> None:
        assert _parse_allowed_origins(" https://a.example , https://b.example ,") == [
            "https://a.example",
            "https://b.example",
        ]

    def test_wildcard_passthrough(self) -> None:
        assert _parse_allowed_origins("*") == ["*"]


class TestCorsDefaultRestrictive:
    def test_no_cors_headers_on_cross_origin_request(self, client: TestClient) -> None:
        """With ALLOWED_ORIGINS unset (test env), no CORS grant is emitted."""
        resp = client.get(
            "/api/v2/health",
            headers={"Origin": "https://other.example"},
        )
        assert resp.status_code in (200, 503)
        assert "access-control-allow-origin" not in resp.headers

    def test_preflight_not_granted(self, client: TestClient) -> None:
        resp = client.options(
            "/api/v2/validate",
            headers={
                "Origin": "https://other.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers
