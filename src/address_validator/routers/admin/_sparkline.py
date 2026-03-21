"""Inline SVG sparkline builder for admin dashboard stat cards."""

from __future__ import annotations

from html import escape

SPARKLINE_CONFIG: dict[str, tuple[str, str]] = {
    # key: (color, aria-label)
    "requests_all": ("#6d4488", "All requests over 30 days"),  # co-purple
    "requests_week": ("#2d9f9f", "Requests over 7 days"),  # teal
    "requests_24h": ("#4a7fbf", "Requests over 24 hours"),  # blue
    "cache_hit_rate": ("#d4882a", "Cache hit rate over 7 days"),  # orange
    "error_rate": ("#c44e8a", "Error rate over 7 days"),  # magenta
}

SPARKLINE_COLORS: dict[str, str] = {k: v[0] for k, v in SPARKLINE_CONFIG.items()}

# SVG dimensions (viewBox units — scales responsively).
_WIDTH = 120
_HEIGHT = 32
_PAD = 2  # vertical padding so strokes aren't clipped


def build_sparkline_svg(
    points: list[float],
    *,
    color: str,
    label: str = "",
    width: int = _WIDTH,
    height: int = _HEIGHT,
) -> str:
    """Build an inline SVG sparkline from a list of values.

    Returns an ``<svg>`` element string with ``role="img"`` and ``aria-label``.
    Empty or all-zero data renders a flat midpoint line with a "No data" label.
    """
    usable_h = height - 2 * _PAD
    mid_y = _PAD + usable_h / 2

    # Empty or all-zero → "No data" flat line.
    if not points or all(v == 0 for v in points):
        return _no_data_svg(color=color, label=label, width=width, height=height, mid_y=mid_y)

    # Constant non-zero → flat line at midpoint (no "No data" label).
    mn, mx = min(points), max(points)
    if mn == mx:
        full_label = f"{label}, steady" if label else "steady"
        return _flat_line_svg(
            color=color,
            label=full_label,
            width=width,
            height=height,
            mid_y=mid_y,
        )

    # Normal case — build polyline.
    trend = _describe_trend(points)
    full_label = f"{label}, {trend}" if label else trend

    n = len(points)
    x_step = width / max(n - 1, 1)
    coords: list[str] = []
    for i, v in enumerate(points):
        x = round(i * x_step, 1)
        # Invert y: higher values → lower y coordinate.
        y = round(_PAD + usable_h - (v - mn) / (mx - mn) * usable_h, 1)
        coords.append(f"{x},{y}")

    polyline = (
        f'<polyline points="{" ".join(coords)}" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return _wrap_svg(polyline, label=full_label, width=width, height=height)


def _describe_trend(points: list[float]) -> str:
    """Return a short trend descriptor: 'trending up', 'trending down', or 'stable'."""
    mid = len(points) // 2
    first_half = sum(points[:mid]) / max(mid, 1)
    second_half = sum(points[mid:]) / max(len(points) - mid, 1)
    if second_half > first_half * 1.1:
        return "trending up"
    if second_half < first_half * 0.9:
        return "trending down"
    return "stable"


def _no_data_svg(*, color: str, label: str, width: int, height: int, mid_y: float) -> str:
    line = (
        f'<line x1="0" y1="{mid_y}" x2="{width}" y2="{mid_y}" '
        f'stroke="{color}" stroke-width="1.5" stroke-opacity="0.3" stroke-dasharray="4 3"/>'
    )
    text = (
        f'<text x="{width / 2}" y="{mid_y + 4}" '
        f'text-anchor="middle" font-size="9" fill="#9ca3af" aria-hidden="true">No data</text>'
    )
    return _wrap_svg(line + text, label=label or "No data", width=width, height=height)


def _flat_line_svg(*, color: str, label: str, width: int, height: int, mid_y: float) -> str:
    line = (
        f'<line x1="0" y1="{mid_y}" x2="{width}" y2="{mid_y}" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
    )
    return _wrap_svg(line, label=label, width=width, height=height)


def _wrap_svg(inner: str, *, label: str, width: int, height: int) -> str:
    safe_label = escape(label, quote=True)
    return (
        f'<svg viewBox="0 0 {width} {height}" '
        f'class="w-full h-8" preserveAspectRatio="none" '
        f'role="img" aria-label="{safe_label}">'
        f"{inner}</svg>"
    )
