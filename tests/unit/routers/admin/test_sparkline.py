"""Tests for sparkline SVG builder."""

from address_validator.routers.admin._sparkline import (
    SPARKLINE_COLORS,
    SPARKLINE_CONFIG,
    build_sparkline_svg,
)


def test_build_sparkline_normal_data() -> None:
    """Normal data produces an SVG with a polyline and trend label."""
    points = [3.0, 7.0, 2.0, 9.0, 5.0]
    svg = build_sparkline_svg(points, color="#6d4488", label="Test sparkline")
    assert "<svg" in svg
    assert 'role="img"' in svg
    assert "Test sparkline" in svg
    assert "<polyline" in svg
    assert "#6d4488" in svg
    # Trend descriptor appended to label.
    assert "trending" in svg or "stable" in svg


def test_build_sparkline_empty_data() -> None:
    """Empty data shows flat line and 'No data' text."""
    svg = build_sparkline_svg([], color="#6d4488", label="Empty")
    assert "<svg" in svg
    assert "No data" in svg
    assert "<line" in svg


def test_build_sparkline_all_zeros() -> None:
    """All-zero data shows flat line and 'No data' text."""
    svg = build_sparkline_svg([0, 0, 0, 0], color="#6d4488", label="Zeros")
    assert "No data" in svg
    assert "<line" in svg


def test_build_sparkline_single_point() -> None:
    """Single data point renders without error."""
    svg = build_sparkline_svg([5.0], color="#6d4488", label="Single")
    assert "<svg" in svg
    assert "#6d4488" in svg
    assert "steady" in svg  # constant (single point) → flat line with "steady"


def test_build_sparkline_constant_nonzero() -> None:
    """Constant non-zero data shows flat line with 'steady' (no 'No data')."""
    svg = build_sparkline_svg([5, 5, 5], color="#2d9f9f", label="Constant")
    assert "No data" not in svg
    assert "steady" in svg
    assert "<line" in svg


def test_build_sparkline_label_escaped() -> None:
    """Labels with special characters are HTML-escaped in aria-label."""
    svg = build_sparkline_svg([1, 2, 3], color="#6d4488", label='Rate "high" & rising')
    assert "Rate &quot;high&quot; &amp; rising" in svg


def test_sparkline_config_has_all_keys() -> None:
    """SPARKLINE_CONFIG has entries for all 5 dashboard cards."""
    expected = {"requests_all", "requests_7d", "requests_24h", "cache_hit_rate", "error_rate"}
    assert set(SPARKLINE_CONFIG.keys()) == expected
    # SPARKLINE_COLORS is derived from CONFIG — same keys.
    assert set(SPARKLINE_COLORS.keys()) == expected
