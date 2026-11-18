"""Shared assertions for admin pagination links (#425)."""

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit


def pagination_query(html: str, label: str = "Next") -> dict[str, list[str]]:
    """Return the parsed query of the pagination link labelled *label* ("Next"/"Prev").

    Blank values are kept so a test can tell ``since=`` apart from an absent param.
    """
    match = re.search(rf'<a href="([^"]*)"[^>]*>(?:&laquo; )?{label}', html)
    assert match, f"no {label} pagination link rendered"
    return parse_qs(urlsplit(unescape(match.group(1))).query, keep_blank_values=True)
